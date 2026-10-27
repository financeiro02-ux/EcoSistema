# Relatório de Contas a Pagar (Streamlit)

## Rodar localmente
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Publicar no Streamlit Community Cloud
1. Suba esta pasta para um repositório no GitHub (`app.py`, `requirements.txt`, `assets/lotus.png`).
2. Em https://share.streamlit.io clique em **Create app**, escolha o repositório e `app.py` como arquivo principal.
3. Clique em **Deploy**.

## Atenção: empresas cadastradas
As empresas novas ficam em `empresas.json`. No Streamlit Community Cloud o disco é
temporário e esse arquivo é apagado quando o app reinicia. A Lotus (padrão) nunca se perde.
Para outras empresas fixas, adicione-as na lista `BUILTIN` do `app.py` (e a logo em `assets/`).
