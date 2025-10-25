# QLD MapServer Parcel Agent (azd template)

- Uses **Address layer (0)** to resolve **lotplan**, then queries **Parcels (4)**.
- KMZ styling: fill/line **#A23F97**, 40% fill, 3px outline.
- Endpoints:
  - `POST /process_pdf_kmz`
  - `GET /kmz_by_lotplan?lotplan=...`
  - `POST /kmz_by_address_fields`

## Deploy (one command)
```bash
curl -fsSL https://aka.ms/install-azd.sh | bash
azd auth login
azd up
```
Set `X_API_KEY` when prompted. Outputs show `apiUrl` for Power Automate.

## Local dev
```bash
cd backend && uvicorn app.main:app --reload --port 8000
cd frontend && npm i && VITE_API_BASE=http://localhost:8000 npm run dev
```

## Deploy to Render (Backend only)
1. Push this repository to GitHub (or another Git provider Render supports).
2. In Render, create a **Web Service**, pick the repo/branch, set the service name (e.g. `QLD_Quote_Mapper`), and choose the **Docker** environment.
3. Add environment variables:
   - `X_API_KEY` – choose the key clients must send (e.g. `Qldmapper2025`).
   - `QLD_MAPSERVER_BASE` – optional; defaults to the QLD Planning Cadastre MapServer.
   - `ARCGIS_AUTH_TOKEN` – optional; leave blank unless you have a token.
4. Deploy; Render will build the Docker image and run `uvicorn` on the port it assigns.

When running the local frontend against the deployed backend, start Vite with:
```bash
cd frontend
VITE_API_BASE=https://qld-quote-mapper.onrender.com npm run dev
```
