import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

# 1. Base inicial de exemplos para ensinar o robô
textos_treino = [
    "placa mae funcionando perfeitamente usada apenas 6 meses na caixa",
    "xbox antigo completo com cabos e controle funcionando tudo 100 por cento",
    "nintendo wii com marcas de uso mas roda jogos perfeito",
    "placa mae liga nao da video para conserto ou aproveitar pecas no estado",
    "leia o anuncio console nao esta lendo disco favor ler o anuncio pecas",
    "esta quebrado nao liga tela trincada com defeito avaria"
]
# 0 = Saudável (Perfeito), 1 = Defeito (Avaria)
rotulos_treino = [0, 0, 0, 1, 1, 1]

print("🧠 Treinando modelo estatístico de NLP...")

# 2. Configura o vetorizador (transforma palavras em pesos numéricos)
vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=1000)
X_treino = vectorizer.fit_transform(textos_treino)

# 3. Treina o classificador
modelo_ia = LogisticRegression()
modelo_ia.fit(X_treino, rotulos_treino)

# 4. Exporta os arquivos que o script principal precisa ler
joblib.dump(vectorizer, 'vetorizador_olx.pkl')
joblib.dump(modelo_ia, 'modelo_ia_olx.pkl')

print("✅ Arquivos 'vetorizador_olx.pkl' e 'modelo_ia_olx.pkl' gerados com sucesso!")