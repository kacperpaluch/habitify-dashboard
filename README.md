# Habit Lens

[![Docker Hub](https://img.shields.io/docker/pulls/kpa90/habitify-dashboard?logo=docker)](https://hub.docker.com/r/kpa90/habitify-dashboard)

Self-hostowany dashboard analityczny dla danych z Habitify. Habitify jest jedynym źródłem prawdy; Habit Lens działa wyłącznie do odczytu, synchronizuje historię przez API v2 i przechowuje lokalny snapshot w SQLite.

## Funkcje

- panel **Dziś** na starcie: co zostało do zrobienia i co jest na szali,
- automatyczna synchronizacja z Habitify co 30 minut,
- ręczna i pełna synchronizacja z interfejsu,
- dzienne i tygodniowe cele z poprawnym przeliczaniem jednostek,
- heatmapa, skuteczność, streaki, trendy 7/30 i porównania okresów,
- reszta analityki zwinięta w sekcji „Analiza historyczna",
- stabilne identyfikatory Habitify — zmiana nazwy nie rozdziela historii,
- brak zależności runtime poza biblioteką standardową Pythona.

## Konfiguracja

Utwórz `.env` na podstawie `.env.example`:

```env
HABITIFY_API_KEY=hb_...
APP_PORT=8080
SYNC_INTERVAL_MINUTES=30
TZ=Europe/Warsaw
```

Klucz wygenerujesz w Habitify w `Settings > API`. Plik `.env` jest ignorowany przez Git i nie jest kopiowany do obrazu Dockera.

## Uruchomienie przez Docker Compose

Gotowy wieloplatformowy obraz jest dostępny na [Docker Hub](https://hub.docker.com/r/kpa90/habitify-dashboard).

Utwórz `.env` na podstawie `.env.example`, uzupełnij `HABITIFY_API_KEY`, a następnie uruchom:

```bash
docker compose up -d
```

Dashboard: <http://localhost:8080>

### Portainer

1. Utwórz nowy Stack i wklej zawartość `docker-compose.yml`.
2. W sekcji **Environment variables** ustaw co najmniej `HABITIFY_API_KEY`.
3. Opcjonalnie ustaw `APP_PORT`, `SYNC_INTERVAL_MINUTES` i `TZ`.
4. Wdróż Stack. Dane SQLite pozostaną w named volume `habitify-dashboard-data`.

Compose domyślnie używa tagu `latest`. Do rollbacku ustaw w polu `image` konkretny tag wersji podany przy wydaniu.

Lokalnie, bez Dockera:

```bash
python3 app.py
```

Przy starcie aplikacja rozpoczyna synchronizację w tle. Można ją też uruchomić przyciskiem `Synchronizuj`.

## Synchronizacja

```text
Habitify API v2
  → GET /habits (aktywne i archiwalne, z paginacją)
  → GET /habits/{id}/statistics dla każdego nawyku
  → normalizacja jednostek i agregacja celów tygodniowych
  → transakcyjny zapis w SQLite
  → dashboard i analityka
```

Pierwsza synchronizacja pobiera historię od `startDate` każdego nawyku. Kolejne odświeżają końcówkę historii z nakładką kilku dni, aby uwzględnić poprawione wpisy. Pełna synchronizacja ponownie pobiera cały zakres.

API aplikacji:

| Metoda | Endpoint | Opis |
|---|---|---|
| `GET` | `/api/health` | healthcheck i stan konfiguracji |
| `GET` | `/api/config` | konfiguracja oraz ostatnia synchronizacja |
| `GET` | `/api/dashboard` | dane dashboardu i analityka |
| `GET` | `/api/habits/{name}` | szczegóły nawyku |
| `GET` | `/api/syncs` | 20 ostatnich synchronizacji |
| `POST` | `/api/sync` | synchronizacja przyrostowa |
| `POST` | `/api/sync?full=1` | pełna synchronizacja |

## Model danych

- `habits` — konfiguracja nawyków ze stabilnymi ID Habitify,
- `records` — dzienne albo zagregowane tygodniowe wyniki,
- `sync_runs` — historia udanych i nieudanych synchronizacji.

Migracja ze starej wersji CSV celowo usuwa poprzednie dane, ponieważ zostały przeniesione do Habitify.

## Testy

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
```

Testy używają atrap odpowiedzi Habitify i nie łączą się z Internetem.

## Bezpieczeństwo

- nie commituj `.env` ani bazy z katalogu `data/`,
- klucz jest używany wyłącznie po stronie backendu w nagłówku `X-API-Key`,
- frontend nigdy nie otrzymuje wartości klucza,
- przy publicznym wdrożeniu zabezpiecz dashboard przez VPN, Cloudflare Access albo reverse proxy z autoryzacją.

Szczegóły implementacyjne: [llm-context.md](llm-context.md).
