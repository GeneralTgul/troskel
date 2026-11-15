# Granskningen inför det offentliga repot

*Vad som söktes i grenens filer innan koden blir offentlig, vad som rättades och varje träff som är kvar med skälet. Gjord 2026-11-15 på grenen `oppen`, över alla spårade filer och bilder. Sidan beskriver sökorden utan att skriva ut dem, så att den inte själv blir en träff.*

<!-- toc -->
## Innehåll

- [Vad som söktes](#vad-som-söktes)
- [Rättat](#rättat)
- [Kvar, och varför det är ok](#kvar-och-varför-det-är-ok)
- [Före den första commiten](#före-den-första-commiten)
<!-- /toc -->

## Vad som söktes

Sökningen gjordes med `git grep` över de spårade filerna, alltså det som följer med en ögonblicksbild (`git archive`). Binära filer söktes för sig.

| Vad | Hur | Utfall |
|---|---|---|
| Ägarens förnamn, efternamn och hemort | Orden, skiftlägesokänsligt för efternamnet och orten | Inga träffar |
| Det privata repot, dess ärendenummer och sökvägar | Repots namn, `ägare/repo`, `#` följt av tre siffror, hemkatalogen, `/config` | Inga träffar |
| Hemmets datorer och tjänster | Datorernas namn, virtualiseringen, hemautomationen, VPN och nätverksutrustningen | Inga träffar |
| E-postadresser | Allt som liknar `namn@domän.tld` | Bara påhittade adresser, se nedan |
| IP-adresser | Fyra tal med punkter, utom `127.0.0.1`, `0.0.0.0` och dokumentationsnätet `192.0.2.0/24` | Se nedan |
| MAC-adresser och Bluetooth-namn | Sex hexpar med kolon, enheternas namnformat | Bara påhittade, se nedan |
| Serienummer | Löpbandets och GoPro-kamerornas riktiga nummer och deras format | Inga riktiga, se nedan |
| Telefonnummer | Svenska nummerformat | Ett påhittat, se nedan |
| Nycklar och token | `api_key`, `secret`, `token`, `password`, `lösenord` följt av ett värde; privata nycklar (PEM), GitHubs, AWS:s, Googles, Slacks och språkmodellernas tokenformat | Bara attrappvärden i testerna |
| Hälsodata om en riktig person | Maxpuls, toppuls, VO2max och klockans värde från maxtestet 2026-11-14 | Bytta mot påhittade värden, se [Rättat](#rättat) |
| Datafiler | `data/`, databaser, FIT-filer, filmer, CSV, PDF och zip | Inga |
| Bilderna | Alla 58 PNG-bilder: metadata (EXIF och textfält), och de 18 som ändrats sedan genomgången av historiken öppnades och lästes | Ingen metadata. Alla visar demoläget: Erik Sandberg, Anna Lindqvist, Sara Holm och simulerade enheter |

Provet [`tests/tools/test_no_private_data.py`](../../tests/tools/test_no_private_data.py) gör de viktigaste sökningarna vid varje körning av testsviten: ägarens namn och hemort, det privata repot, hemmets datorer, de riktiga serienumren, adresser i hemnätet (`192.168.*`) och privata e-postdomäner.

## Rättat

- **Maxtestet 2026-11-14:** testfallen och handboken använde den riktiga löparens sparade maxpuls, toppulsen i testet, klockans VO2max och det uppskattade VO2max. De är bytta mot påhittade värden som prövar samma regler: en topp under den sparade maxpulsen ger frågan *Behåll …?*, en högre topp höjer, och ett brant sista steg ger inget VO2max. Klockans värde i provet för varningen ligger fortfarande mer än 10 % från uppskattningen.
- **Hänvisningar till den privata miljön:** ärendenummer, datornamn, namn i citat, filnamn i ett prov och beskrivningar av vilka webbplatser sökverktyget når är borta eller omskrivna till skälet.
- **Telefonnumret** i demodatan är nu ett nummer ur Post- och telestyrelsens serie för fiktiva nummer.
- **Workflowen** har inget steg som publicerar felloggar, och loggfilen som steget läste skrivs inte längre.

## Kvar, och varför det är ok

| Träff | Var | Skäl |
|---|---|---|
| `anna@exempel.se`, `name@example.com`, adresser på `example.test`, `exempel.se`, `example.se`, `a@b.se` och liknande | Demodatan, språkfilernas exempel och testerna | Påhittade adresser. `example.*` är reserverade för exempel |
| `070-174 06 50` | Demons varumärke (*Nordisk Löpcoaching*, *Anna Berg*) och två prov | I serien för fiktiva nummer, 070-174 06 05 till 070-174 06 99 |
| `10.0.0.5`, `10.0.0.9` med `admin:hemligt`, `u:p` och `user:pw` | Kameraproven | Påhittade adresser i ett privat nät, och provet visar att lösenordet tas bort ur adressen |
| `172.2x.1xx.51` | GoPro-proven | Räknas fram ur ett påhittat serienummer på samma sätt som GoPro gör över USB |
| `224.0.0.251` | `camera/network.py` | mDNS-gruppens standardadress |
| `C3000000000830`, `C3000000000548`, `C3441325123456` | GoPro-proven | Påhittade serienummer i GoPros format |
| `SW1000EAI-0001`, `HRM-Pro+:123456`, `PM5 430123456`, `Tacx Neo 2T 12345` | Enhetsproven | Påhittade Bluetooth-namn i tillverkarnas format |
| `AA:BB:CC:DD:EE:FF`, `11:22:33:44:55:66`, `C0:FF:EE:00:00:01` | Enhetsproven | Påhittade MAC-adresser |
| `abcd efgh ijkl mnop`, `fel-losenord-xyz`, `något annat`, `Zebraknä-anteckning-4711` | Proven för e-post, säkerhetskopian och raderingen | Attrappvärden som proven letar efter |
| `${{ github.token }}` | Workflowen | GitHubs eget token för körningen, inget värde i repot |
| Datum som *2026-11-14* och *riktig hårdvara* | Kommentarer, prov och [PROV.md](../PROV.md) | Säger när något provades, utan person eller plats |
| Maxpuls 185 i allmänna räkneexempel | VO2max-proven | Ett vanligt exempelvärde som räknas om i provet (Uth–Sørensen), inte kopplat till en person |
| *Windows-minuter räknas dubbelt i ett privat repo* | Kommentar i workflowen | Skälet till att Windows-bygget bara kör proven som skiljer sig på Windows. Stämmer inte längre i ett offentligt repo, men beteendet är detsamma |
| *(läsaren)* som märkning av källor | [Bikefit och stakteknik](../sporter/bikefit-och-stakteknik.md), [Enheterna](../sporter/enheter.md) | Betyder *bekräftat i en webbsökning*, och står förklarat på sidorna |
| ErgZone, ErgData, Zwift, QZ, Garmin | Handboken och koden | Allmänt kända appar och tjänster som programmet samverkar med |

## Före den första commiten

- Den här sidan beskriver granskningen och kan följa med, eller tas bort innan den rena commiten görs.
- Den rena commiten görs med GitHubs `noreply`-adress, så att ingen privat e-postadress följer med.
- `docs/handbok/*/images/` ska tas om med `python tools/docs_shots.py` om gränssnittet ändras innan dess.
