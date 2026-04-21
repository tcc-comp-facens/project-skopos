# multiagent-frontend

Interface web React + Vite para o sistema de comparação de arquiteturas multiagente.

## Pré-requisitos

- Node.js 18+
- npm 9+

## Instalação

```bash
npm install
```

## Configuração

Copie o arquivo de exemplo e ajuste as variáveis:

```bash
cp .env.example .env
```

| Variável | Descrição | Padrão |
|---|---|---|
| `VITE_API_URL` | URL base do backend REST | `http://localhost:8000` |
| `VITE_WS_URL` | URL base do WebSocket do backend | `ws://localhost:8000` |

## Execução

### Desenvolvimento

```bash
npm run dev
```

A aplicação estará disponível em `http://localhost:5173`.

### Build de produção

```bash
npm run build
npm run preview
```

## Testes

```bash
# Execução única
npm test

# Modo watch
npm run test:watch
```

Os testes usam [Vitest](https://vitest.dev/) + [@testing-library/react](https://testing-library.com/docs/react-testing-library/intro/) e [fast-check](https://fast-check.io/) para testes baseados em propriedades.

## Estrutura

```
src/
├── main.tsx              # Ponto de entrada
├── App.tsx               # Componente raiz
├── components/
│   ├── AnalysisControls.tsx   # Controles de entrada (período + toggles + botão)
│   └── ArchitecturePanel.tsx  # Painel de resultado por arquitetura
├── hooks/
│   └── useWebSocket.ts        # Hook para conexão WebSocket com reconexão automática
└── types/
    └── index.ts               # Interfaces TypeScript (AnalysisRequest, WSEvent, etc.)
```
