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
