# ChipVerify Desktop Frontend

React + Vite frontend for ChipVerify AI Studio desktop/on-prem deployments.

## Local development

```bash
npm install
npm run dev
```

## Production backend URL

Set `VITE_API_URL` to backend base URL only.

- Correct: `https://chipverify.example.com`
- Incorrect: `https://chipverify.example.com/api/v1`

The app appends `/api/v1/...` paths internally.

Use `frontend/.env.production.example` as template.

## Build

```bash
npm run build
```
