# LLM Context — Habit Lens

Kod jest źródłem prawdy przy każdej rozbieżności z tym dokumentem.

## Produkt i stack

Habit Lens to jednoosobowy, self-hostowany dashboard analityczny dla Habitify. Integracja jest read-only. Backend używa Python 3.12, `ThreadingHTTPServer`, `urllib.request` i SQLite. Frontend to HTML, CSS, vanilla JavaScript i Canvas 2D. Nie ma zależności PyPI ani npm.

Założenia:

- Habitify jest jedynym źródłem prawdy,
- jeden użytkownik i jeden kontener,
- frontend oraz API mają ten sam origin,
- klucz API istnieje wyłącznie po stronie backendu,
- dane trwałe znajdują się w named volume `/app/data`.

## Konfiguracja

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `HABITIFY_API_KEY` | brak | wymagany klucz Habitify |
| `HABITIFY_BASE_URL` | `https://api.habitify.me/v2` | baza API |
| `HABITIFY_TIMEOUT` | `30` | timeout requestu w sekundach |
| `SYNC_INTERVAL_MINUTES` | `30` | okres synchronizacji; `0` wyłącza automat |
| `SYNC_OVERLAP_DAYS` | `8` | nakładka synchronizacji przyrostowej |
| `BACKUP_TIME` | `03:00` | godzina codziennego backupu według `TZ` |
| `BACKUP_KEEP` | `14` | liczba zachowywanych kopii |
| `MAX_BACKUP_MB` | `100` | limit uploadu bazy do restore |
| `DB_PATH` | `data/habits.db` | ścieżka SQLite |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | serwer HTTP |

Prosty parser ładuje lokalny `.env` bez nadpisywania istniejących zmiennych procesu.

## Architektura

```text
auto_sync_loop / POST /api/sync
  → sync_habitify
    → fetch_habits (active + archived, paginacja)
    → GET statistics per habit
    → records_from_statistics
    → jedna transakcja SQLite

GET /api/dashboard
  → query_records (zakres + zakres poprzedni + zakres bez dat)
  → agregaty i extended_analytics
  → static/app.js
```

`dashboard` wykonuje trzy odczyty `records` niezależnie od liczby nawyków. Zapytanie bez filtra dat zasila zarówno streaki, jak i `extended_analytics`; nie wolno wracać do odpytywania per nawyk.

`SYNC_LOCK` zapobiega równoległym synchronizacjom z przycisku i wątku automatycznego. Każdy wątek otwiera osobne połączenie SQLite.

Niezależny `backup_loop` sprawdza harmonogram co minutę. Po `BACKUP_TIME` tworzy najwyżej jeden backup `scheduled` danego dnia, o ile istnieją rekordy. Kopia korzysta z online backup API SQLite i przechodzi kontrolę nagłówka, `PRAGMA integrity_check`, schematu, `PRAGMA user_version >= 2` oraz SHA-256. Kontrola wersji jest istotna dla restore: kończy się on `init_db()`, który przy starszym schemacie skasowałby tabele, więc taka baza musi odpaść przed podmianą, a nie po niej. Restore tworzy najpierw kopię `pre-restore`; pliki są przechowywane w `/app/data/backup`.

Snapshot jest przełączany na `journal_mode=DELETE`, więc sam `.db` jest kompletnym backupem. Odczyt walidacyjny i restore używają `immutable=1`, a sidecary `-wal`/`-shm` snapshotów są sprzątane. Sidecary aktywnej bazy `/app/data/habits.db` pozostają normalnym elementem WAL.

## Schemat v2

- `sync_runs`: czas, status, full/incremental, liczniki zmian i komunikat błędu.
- `habits`: aktualna konfiguracja, klucz główny równy stabilnemu ID Habitify.
- `records`: klucz `(date, habit_id, period)` i zdenormalizowane metadane nawyku. Bazy sprzed usunięcia kolumny `note` zachowują ją jako szczątkową; `DEFAULT ''` sprawia, że INSERT bez niej działa w obu schematach i migracja nie jest potrzebna.

Rename aktualizuje wszystkie rekordy danego `habit_id`. Rekord dzienny oznacza dzień, a tygodniowy cały tydzień ISO i używa daty poniedziałku.

Migracja ze schematu CSV jest świadomie destrukcyjna: usuwa stare `imports` oraz `records`, po czym buduje tabele v2 i ustawia `PRAGMA user_version=2`.

## Mapowanie Habitify

| Habitify | Habit Lens |
|---|---|
| `id` | `habit_id` |
| `type=good` | `Building` |
| `type=bad` | `Breaking` |
| aktywny `goal.periodicity` | `Daily` / `Weekly` |
| `areas[].name` | `list_name` |
| `dailyProgress.totalLog` | `quantity` po normalizacji |
| `status=completed` | `Complete` |
| pozostałe statusy | `Incomplete` |

API statystyk zwraca niektóre wartości w jednostkach bazowych:

- `duration` w sekundach: minuty dzielimy przez 60, godziny przez 3600,
- `mass` w kilogramach: gramy mnożymy przez 1000, miligramy przez 1 000 000,
- `energy` w dżulach: kcal dzielimy przez 4184,
- `scalar` bez konwersji.

Dla mierzalnego Breaking z niezerowym limitem backend porównuje wartość z celem, ponieważ Habitify potrafi oznaczyć każdy dodatni log jako `failed`, nawet poniżej limitu. Status `skipped` pozostaje niezaliczony. Dla celu tygodniowego API zwraca dzienne składowe; backend sumuje je według tygodnia ISO. Building jest zaliczony dla `quantity >= goal`, Breaking dla `quantity <= goal`.

## Synchronizacja

Pierwszy sync pobiera `habit.startDate..today`. Sync przyrostowy zaczyna od ostatniej daty minus `SYNC_OVERLAP_DAYS`. `full=1` pobiera pełny zakres. Data startu jest dosuwana do początku okresu (`period_key`), bo rekord tygodniowy jest nadpisywany w całości — start w środku tygodnia przeliczyłby go z niepełnego zakresu i zaniżył sumę.

Sieć jest odpytywana przed transakcją zapisu. Dopiero komplet poprawnych odpowiedzi jest zapisywany atomowo. Błąd pozostawia poprzedni snapshot i tworzy `sync_runs.status=failed`. Nawyki nieobecne w pełnej liście active+archived są usuwane lokalnie przez `ON DELETE CASCADE`.

## Lokalne API

- `GET /api/health`
- `GET /api/config`
- `GET /api/dashboard`
- `GET /api/habits/{name}`
- `GET /api/syncs`
- `POST /api/sync[?full=1]`
- `GET /api/backups`
- `POST /api/backup`
- `GET /api/backups/{file}/download`
- `POST /api/backups/{file}/restore`
- `POST /api/backups/restore-upload`

`GET /api/syncs` i `GET /api/backups` obsługują `page`, `per_page` (maks. 50), `date_from` i `date_to`, zwracając metadane `pagination`. Modal ładuje jedną zakładkę historii naraz.

Filtry dashboardu: `start`, `end`, `habit`, `list`, `period`.

## Reguły analityczne

- `Complete` jest jedynym statusem sukcesu.
- Nie mieszać Daily i Weekly w trendach ani streakach.
- Bieżący nieukończony dzień lub tydzień ma grace period.
- `analytics.today` obejmuje **każdy** otwarty okres, także przy streaku 0; nawyk, który już się sypie, jest najważniejszy do pokazania. Brak rekordu na bieżący okres oznacza „nie zaplanowano" i nie trafia do `pending`.
- Heatmapa korzysta tylko z rekordów Daily.
- Cel `Breaking=0` nie może być dzielnikiem. Dla `Breaking` z limitem > 0 okres bez logów daje stosunek 999%, bo dzielenie przez zero nie ma sensownego wyniku, a sufit trzyma średnią w skali.
- Współwystępowanie nie oznacza przyczynowości; wiarygodność od 30 wspólnych dni.
- `inprogress` bieżącego dnia lub tygodnia jest stanem `in_progress`: nie zwiększa licznika porażek ani mianownika skuteczności, dopóki okres się nie zakończy. Osobno `is_running` odpowiada na pytanie, czy okres jeszcze trwa, niezależnie od statusu: `average`, `minimum` i `maximum` pomijają trwający okres także wtedy, gdy cel już w nim padł, bo suma dalej rośnie. `latest` celowo pokazuje bieżący okres.

## Weryfikacja

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
docker compose config
docker compose up -d --build
curl --fail http://localhost:8080/api/health
```

Testy mockują `habitify_request` i nigdy nie korzystają z prawdziwego klucza. Nie wolno logować ani zwracać `HABITIFY_API_KEY` w endpointach.
