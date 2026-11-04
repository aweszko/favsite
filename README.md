# Triumfator – Porównywarka statystyk piłkarskich

![Triumfator](static/logo.png)

Strona w motywie grafitowo‑złotym (srebrne dodatki, bordo jako akcent; mobile‑first) z dwiema sekcjami:

1. **Mecze** – zakładki jak w Superbet/STS: *Live*, *Za 1 godz.*, *Za 3 godz.*, *Dziś*, *Jutro* i kolejne dni (łącznie 7 dni).
   Wszystkie ligi piłkarskie z oferty Superbet PL, pogrupowane wg rozgrywek, z godziną (Europe/Warsaw), drużynami i kursami 1X2.
   Najniższy kurs jest podświetlony. Lista ładuje się szybko (bez pobierania statystyk).
2. **Faworyci – ranking** – faworyci (kurs w zakresie, przewaga ≥ 0,50 nad rywalem) sortowani wg strzałów celnych
   z ostatnich 5 meczów. Każda karta: **faworyt vs rywal** – kursy, strzały celne/ogółem (średnio i Σ), celność,
   różnica (zielona/czerwona) oraz rozwijane ostatnie 5 meczów obu drużyn. Tryby: Live / Przedmeczowe (dziś) / Wszystkie,
   filtry: min. meczów z danymi, kurs od–do. Brak danych = „brak danych” (nic nie jest zmyślane).

## Uruchomienie lokalne
Wymagany Python 3.9+ (bez instalowania pakietów).
```
python app.py          # http://localhost:8000
PORT=9000 python app.py
```
Windows: `start.bat`, Linux/macOS: `./start.sh`.

## Wdrożenie (np. Render, darmowy plan)
Pliki gotowe: `render.yaml`, `Procfile`, `requirements.txt` (pusty), `.gitignore`.
Aplikacja czyta zmienną `PORT`, nasłuchuje na `0.0.0.0`, health check: `/healthz`. Brak sekretów i kluczy.
Wystarczy wrzucić folder do repozytorium i utworzyć Web Service z `render.yaml`.
Uwaga: darmowy plan usypia serwis – pierwsze wejście po przerwie i pierwsze wyliczenie rankingu trwa dłużej.

## Grafika
Logo wycięte z dostarczonego pliku rastrowego (przezroczyste tło, bez przeróbek generatywnych): `static/logo.png` (600 px, z napisem),
`static/icon.png` (192×192, sam herb), `static/apple-touch-icon.png`, `static/favicon.png`, `static/favicon.ico`.
Serwowane pod `/logo.png`, `/icon.png`, `/favicon.png`, `/apple-touch-icon.png`, `/favicon.ico` (cache 7 dni).

## API
- `/api/matches` – mecze live + 7 dni (cache 55 s)
- `/api/ranking?mode=live|prematch|all&min_games=3&odds_min=1.2&odds_max=2.0`

## Źródła
Superbet PL (oferta, kursy), Sportradar gismo (statystyki strzałów). Dane publiczne, bez logowania.

## Testy
`python test_app.py` (dane przykładowe, bez sieci).
