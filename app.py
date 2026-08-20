# -*- coding: utf-8 -*-
import streamlit as st
import requests
import time
import re
import zipfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import io

# --- CONFIGURAÇÕES DA PÁGINA ---
st.set_page_config(page_title="Tradutor Bilíngue IA", page_icon="📚", layout="wide")

# --- INICIALIZAR O COFRE (SESSION STATE) ---
if "epubs_prontos" not in st.session_state:
    st.session_state.epubs_prontos = False
    st.session_state.epub_1 = None
    st.session_state.epub_2 = None
    st.session_state.epub_3 = None

if "texto_rapido_pronto" not in st.session_state:
    st.session_state.texto_rapido_pronto = ""

# --- FUNÇÕES DO MOTOR ---
def formatar_html(texto):
    # Apenas limpa quebras de linha sujas. Como usamos <p>, não precisamos mais de <br/>
    texto = texto.replace('\n', '')
    return texto.strip()

def inverter_linhas(texto_html):
    # Procura os parágrafos em inglês e português e inverte a ordem
    padrao = re.compile(r'(<p\b[^>]*lang="en"[^>]*>.*?</p>)\s*(<p\b[^>]*lang="pt"[^>]*>.*?</p>)', re.IGNORECASE | re.DOTALL)
    texto_invertido = padrao.sub(r'\2\n\1', texto_html)
    
    # Plano B caso a IA esqueça o lang="en" e use apenas a class="en"
    if texto_invertido == texto_html:
        padrao_fallback = re.compile(r'(<p\b[^>]*class="en"[^>]*>.*?</p>)\s*(<p\b[^>]*class="pt"[^>]*>.*?</p>)', re.IGNORECASE | re.DOTALL)
        texto_invertido = padrao_fallback.sub(r'\2\n\1', texto_html)
        
    return texto_invertido

def extrair_portugues(texto_html):
    # Extrai APENAS os parágrafos em português
    sentencas = re.findall(r'(<p\b[^>]*lang="pt"[^>]*>.*?</p>)', texto_html, re.IGNORECASE | re.DOTALL)
    if not sentencas:
        sentencas = re.findall(r'(<p\b[^>]*class="pt"[^>]*>.*?</p>)', texto_html, re.IGNORECASE | re.DOTALL)
    
    texto_final = "\n".join(sentencas)
    
    # "Lava" a cor verde (style) para que o livro 100% PT fique com o texto preto original
    texto_final = re.sub(r'style="[^"]*"', '', texto_final)
    return texto_final

def gerar_epub_memoria(titulo, html_miolo, css):
    epub_buffer = io.BytesIO()
    epub_id = str(uuid.uuid4())
    
    container_xml = '<?xml version="1.0"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n<rootfiles>\n<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n</rootfiles>\n</container>'
    
    # 1. Arquivo NCX (Índice de navegação exigido pelo EPUB 2 e Voice Dream)
    toc_ncx = f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
    <head>
        <meta name="dtb:uid" content="urn:uuid:{epub_id}"/>
        <meta name="dtb:depth" content="1"/>
        <meta name="dtb:totalPageCount" content="0"/>
        <meta name="dtb:maxPageNumber" content="0"/>
    </head>
    <docTitle><text>{titulo}</text></docTitle>
    <navMap>
        <navPoint id="navPoint-1" playOrder="1">
            <navLabel><text>Início</text></navLabel>
            <content src="content.xhtml"/>
        </navPoint>
    </navMap>
</ncx>'''

    # 2. Content OPF (Padrão 2.0 com mapa NCX)
    content_opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
        <dc:title>{titulo}</dc:title>
        <dc:language>pt-BR</dc:language>
        <dc:identifier id="BookId" opf:scheme="UUID">urn:uuid:{epub_id}</dc:identifier>
    </metadata>
    <manifest>
        <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
        <item id="style" href="style.css" media-type="text/css"/>
        <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
    </manifest>
    <spine toc="ncx">
        <itemref idref="content"/>
    </spine>
</package>'''

    # 3. XHTML ajustado com Declaração de Idioma (xml:lang e lang)
    xhtml_final = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="pt-BR" lang="pt-BR">
<head><title>{titulo}</title><link href="style.css" rel="stylesheet" type="text/css"/></head>
<body>{html_miolo}</body></html>'''

    with zipfile.ZipFile(epub_buffer, 'w', zipfile.ZIP_DEFLATED) as epub:
        epub.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        epub.writestr('META-INF/container.xml', container_xml)
        epub.writestr('OEBPS/content.opf', content_opf)
        epub.writestr('OEBPS/toc.ncx', toc_ncx)
        epub.writestr('OEBPS/style.css', css)
        epub.writestr('OEBPS/content.xhtml', xhtml_final)
    return epub_buffer.getvalue()

def traduzir_bloco(texto, bloco_id, api_key):
    time.sleep(min(bloco_id * 0.5, 5.0)) 
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'}
    # NOVO PROMPT: Exigindo parágrafos (<p>) e declaração de idioma (lang)
    prompt = """Voce e um tradutor especializado em textos intercalados (ingles ↔ portugues).
INSTRUCOES OBRIGATORIAS:
1. Divida o texto em SENTENCAS INDIVIDUAIS.
2. Formato OBRIGATORIO para CADA sentenca:
   <p lang="en" class="en" style="color: #2c3e50; font-weight: bold;">SENTENCA EM INGLES</p>
   <p lang="pt" class="pt" style="color: #27ae60; font-weight: bold;">TRADUCAO EM PORTUGUES</p>
3. NUNCA agrupe multiplas sentencas em um unico bloco. Use a estrutura acima para cada frase.
4. OBRIGATÓRIO: Retorne APENAS o código HTML puro, sem blocos de markdown."""

    data = {
        "model": "deepseek/deepseek-chat",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Texto para traduzir:\n\n{texto}"}
        ],
        "temperature": 0.2,
        "max_tokens": 4000
    }
    
    try:
        r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data, timeout=120)
        r.raise_for_status()
        resultado_limpo = r.json()['choices'][0]['message']['content'].replace("```html", "").replace("```", "").strip()
        return (bloco_id, resultado_limpo)
    except Exception:
        return (bloco_id, None)

def processar_texto_em_blocos(texto_original, api_key_input, interface_texto):
    paragrafos = [p for p in texto_original.replace('\r', '').split('\n') if p.strip()]
    blocos, bloco_atual, contagem = [], [], 0
    for p in paragrafos:
        pal_p = len(p.split())
        if contagem + pal_p <= 500:
            bloco_atual.append(p)
            contagem += pal_p
        else:
            if bloco_atual: blocos.append('\n\n'.join(bloco_atual))
            bloco_atual = [p]
            contagem = pal_p
    if bloco_atual: blocos.append('\n\n'.join(bloco_atual))
    
    total_blocos = len(blocos)
    barra_progresso = interface_texto.progress(0)
    status_texto = interface_texto.empty()
    status_texto.text(f"Iniciando 0/{total_blocos} blocos...")
    
    resultados = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(traduzir_bloco, bloco, i+1, api_key_input): i for i, bloco in enumerate(blocos)}
        concluidos = 0
        for future in as_completed(futures):
            bloco_id, traducao = future.result()
            resultados[bloco_id] = traducao
            concluidos += 1
            barra_progresso.progress(concluidos / total_blocos)
            status_texto.text(f"Traduzindo: {concluidos}/{total_blocos} blocos concluídos")

    status_texto.empty()
    barra_progresso.empty()
    return resultados, total_blocos

# --- INTERFACE WEB STREAMLIT ---
st.title("📚 Central de Tradução IA")
st.markdown("Converta livros inteiros em formato bilíngue ou traduza e-mails e artigos rapidamente na tela.")

# Código embutido para remover o aviso de 200MB da tela
st.markdown("""<style>div[data-testid="stFileUploadDropzone"] * { font-size: 16px; } div[data-testid="stFileUploadDropzone"] small { display: none !important; visibility: hidden !important; }</style>""", unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Configurações")
    api_key_input = st.text_input("Chave API (OpenRouter):", type="password", help="Insira sua chave.")
    st.markdown("---")
    st.markdown("### 💰 Seus Créditos")
    st.info("**Saldo atual:** 5.000 Palavras\n\n*(Este é um simulador)*")

# CRIANDO AS DUAS ABAS
aba1, aba2 = st.tabs(["📄 Traduzir Texto Rápido (Copiar e Colar)", "📂 Converter Livro (.txt para .epub)"])

# ABA 1: TEXTO RÁPIDO NA TELA
with aba1:
    st.header("Tradução Imediata")
    texto_colado = st.text_area("Cole aqui o texto em inglês (e-mails, artigos, trechos):", height=250)
    
    if st.button("Tradução Rápida ⚡", type="primary", key="btn_rapido"):
        if not api_key_input:
            st.warning("Insira sua chave da API na barra lateral.")
        elif not texto_colado:
            st.warning("Cole algum texto para traduzir.")
        else:
            with st.spinner("Analisando o texto..."):
                area_status = st.container()
                resultados, total = processar_texto_em_blocos(texto_colado, api_key_input, area_status)
                
                texto_final_html = ""
                for i in range(1, total + 1):
                    trad = resultados.get(i)
                    if trad:
                        texto_final_html += formatar_html(trad) + "\n\n"
                
                st.session_state.texto_rapido_pronto = texto_final_html

    if st.session_state.texto_rapido_pronto:
        st.success("✅ Tradução concluída!")
        st.markdown("---")
        st.markdown(st.session_state.texto_rapido_pronto, unsafe_allow_html=True)
        st.markdown("---")
        st.caption("Você pode selecionar o texto acima, dar um Ctrl+C e colar no Word ou e-mail.")


# ABA 2: MODO LIVRO (GERAR EPUB)
with aba2:
    st.header("Fábrica de E-books")
    arquivo_upload = st.file_uploader("📂 Faça o upload do seu livro (.txt)", type=['txt'])

    if arquivo_upload is not None:
        texto_original = arquivo_upload.read().decode('utf-8')
        total_palavras = len(texto_original.split())
        st.success(f"Arquivo carregado! Tamanho estimado: **{total_palavras} palavras**.")
        
        if st.button("🚀 Iniciar Conversão para Livro", type="primary", key="btn_livro"):
            if not api_key_input:
                st.warning("Insira sua chave da API na barra lateral.")
            else:
                area_status_livro = st.container()
                resultados, total_blocos = processar_texto_em_blocos(texto_original, api_key_input, area_status_livro)
                
                with st.spinner("Montando os livros..."):
                    miolo_en_pt, miolo_pt_en, miolo_pt = "", "", ""
                    for i in range(1, total_blocos + 1):
                        trad = resultados.get(i)
                        if trad:
                            miolo_en_pt += f'<div class="bloco"><h2>Trecho {i}</h2>\n{formatar_html(trad)}\n</div>\n'
                            miolo_pt_en += f'<div class="bloco"><h2>Trecho {i}</h2>\n{formatar_html(inverter_linhas(trad))}\n</div>\n'
                            miolo_pt += f'<div class="bloco"><h2>Trecho {i}</h2>\n{extrair_portugues(trad)}\n</div>\n'

                    # CSS base mantido simples, as cores agora vêm da IA nos blocos bilíngues
                    css_base = "body { font-family: sans-serif; font-size: 18px; padding: 20px;} .bloco { padding: 25px; margin: 20px 0;}"

                    st.session_state.epub_1 = gerar_epub_memoria("EN-PT", miolo_en_pt, css_base)
                    st.session_state.epub_2 = gerar_epub_memoria("PT-EN", miolo_pt_en, css_base)
                    st.session_state.epub_3 = gerar_epub_memoria("PT Somente", miolo_pt, css_base)
                    st.session_state.epubs_prontos = True

    if st.session_state.epubs_prontos:
        st.success("🎉 Arquivos prontos na memória! Faça os downloads abaixo:")
        st.markdown("---")
        
        # AVISO PARA OS USUÁRIOS (O DISCLAIMER ELEGANTE)
        st.info("💡 **Dica de Compatibilidade:** Nossos arquivos são estruturados no padrão universal de acessibilidade. Caso o seu aplicativo de leitura (como o Voice Dream) apresente comportamento inesperado, recomendamos a leitura em plataformas como Apple Books, Calibre, Thorium ou Kindle.")
        st.markdown("---")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.download_button("📘 Baixar (Inglês - Português)", data=st.session_state.epub_1, file_name="01_EN_PT.epub", mime="application/epub+zip")
        with col2:
            st.download_button("📗 Baixar (Português - Inglês)", data=st.session_state.epub_2, file_name="02_PT_EN.epub", mime="application/epub+zip")
        with col3:
            st.download_button("📕 Baixar (Só Português)", data=st.session_state.epub_3, file_name="03_PT_Only.epub", mime="application/epub+zip")
