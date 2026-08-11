# Habitify Dashboard (Habit Lens)

Self-hostowany, tylko do odczytu dashboard analityczny dla danych z Habitify.

## Najważniejsze funkcje

- automatyczna i ręczna synchronizacja z Habitify API v2,
- heatmapa, skuteczność, streaki oraz trendy 7/30 dni,
- obsługa celów dziennych i tygodniowych,
- trwające dni i tygodnie bez fałszywych niepowodzeń,
- automatyczne i ręczne backupy SQLite z bezpiecznym przywracaniem,
- lokalny snapshot SQLite przechowywany w trwałym wolumenie,
- lekki obraz bez zależności runtime poza standardową biblioteką Pythona,
- obrazy dla `linux/amd64` i `linux/arm64`.

## Uruchomienie

```yaml
services:
  habitify-dashboard:
    image: kpa90/habitify-dashboard:latest
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      HABITIFY_API_KEY: wpisz_swoj_klucz
      SYNC_INTERVAL_MINUTES: 30
      BACKUP_TIME: "03:00"
      BACKUP_KEEP: 14
      MAX_BACKUP_MB: 100
      TZ: Europe/Warsaw
    volumes:
      - habitify-dashboard-data:/app/data

volumes:
  habitify-dashboard-data:
```

Dashboard będzie dostępny pod adresem `http://localhost:8080`.

Klucz API jest używany wyłącznie przez backend i nie trafia do frontendu. Przy publicznym wdrożeniu zabezpiecz usługę przez VPN, Cloudflare Access albo reverse proxy z autoryzacją.
