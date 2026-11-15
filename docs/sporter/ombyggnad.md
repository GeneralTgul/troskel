# Flera sporter: var löpningen sitter och hur programmet byggs om

*Plan och nuläge för fler sporter. Programmet byggdes för löpning på band. Det har nu också cykel (trainer, watt) och SkiErg (Concept2, watt och tempo per 500 m), med egna tester, zoner och rapporter per sport: se [Nuläget](#nuläget). Bikefit och stakteknik (steg 3–5) byggs för sig. Här står också var löpningen var hårdkodad, målbilden, databasmigreringen och stegordningen. Radnumren i avsnitt 1 gäller commit `59ad3ca`, före steg 0.*

<!-- toc -->
## Innehåll

- [Sammanfattning](#sammanfattning)
- [Nuläget](#nuläget)
- [1. Var löpningen är hårdkodad](#1-var-löpningen-är-hårdkodad)
  - [core](#core)
  - [assessments](#assessments)
  - [devices](#devices)
  - [pose](#pose)
  - [storage](#storage)
  - [report](#report)
  - [ui](#ui)
  - [i18n](#i18n)
  - [tester](#tester)
- [2. Målbild](#2-målbild)
- [3. Databasmigrering](#3-databasmigrering)
- [4. Stegordning](#4-stegordning)
- [5. Risker och det som bör vänta](#5-risker-och-det-som-bör-vänta)
- [6. Uppskattning för steg 0](#6-uppskattning-för-steg-0)
<!-- /toc -->

## Sammanfattning

- **Löpningen sitter i fyra saker:** belastningen heter `speed_kmh` med `incline_pct` överallt, ergometern heter `Treadmill`, tekniken heter `gait`, och tempot räknas som min/km. Nästan allt annat (bussen, testkörningen, laktatmatematiken, pulszonerna, rapportmotorn, kameran) är redan allmänt.
- **Sporten är ett värde på testet, och en profil säger vad den betyder.** `core/sport.py` har `Sport` och `SportProfile` med storhet, enhet, tempoformel, ergometer och vilka enhetslägen som styr belastningen.
- **Namnen `speed_kmh` ligger kvar.** Ny kod läser `load` och tar enheten ur profilen. För cykel och SkiErg bär fältet watt. Det stora namnbytet väntar tills bandet, laktattrappan och rapporten är provade på gymdatorn, eftersom proven kan ändra just de filer som namnbytet går igenom.
- **Cykel och SkiErg går att välja** i guiden, med egna tester, enheter, livevy, resultat och rapport. Inget av det har mött riktig hårdvara, se [PROV.md](../PROV.md#cykeltrainern).
- **Löpningen är oförändrad:** samma tester och samma rapport: rapportens HTML för laktattrappa, maxpuls, löpteknik och kombinerat är byte för byte densamma som på huvudgrenen `acbf2cb`, på svenska och engelska. I guiden syns listan *Sport* i steget *Testtyp*.

## Nuläget

Byggt på grenen `cykel-skierg` (2026-11-14). Steg 0a och delar av 0b–0d ligger på huvudgrenen sedan samma dag.

**Det som finns:**

| Del | Vad | Var |
|---|---|---|
| Sport och profil | `Sport` (`run`, `bike`, `skierg`) och en `SportProfile` per sport: storhet, enhet, decimaler, lutning, tempo, ergometer, enhetslägen (`load_sources`), och skalan som körningen och analysen behöver: lugn belastning efter utmattning, när idrottaren räknas som stilla, hur nära två belastningar får ligga, zongränsen bortom testet och bruset i kurvans förskjutning. Alla tre sporterna går att välja | `core/sport.py` |
| Sport på testet | `Session.sport`, `Protocol.sport` och `TestPlan.sport`. Ett test eller protokoll som sparats utan sport läses som löpning. `TestKind.FTP` är nytt (värdet `ftp`) | `core/models.py`, `assessments/protocol.py`, `ui/context.py` |
| Enheterna | `Ergometer` är gränssnittet körningen behöver, med två egenskaper: `controllable` (programmet sätter belastningen) och `measured` (värdena är avlästa). `ErgometerState` bär watt i `speed_kmh` och har effekt, kadens, tempo per 500 m och dragtider bredvid. Trainern (`FtmsBike`: Indoor Bike Data, Set Target Power, simulering för fritt steg), Concept2 PM5 (`Pm5Ergometer`: belastningen läses, och testets steg läggs in som ett pass på monitorn med CSAFE, `csafe.py`), `ManualErgometer`, `SimBike` och `SimSkiErg`. `DeviceManager.connect_ergometer(sport, läge)` och `scan_ergometers(sport)` | `devices/base.py`, `ftms_bike.py`, `pm5.py`, `manual.py`, `sim.py`, `manager.py`, se [enheter.md](enheter.md) |
| Protokollen | Cykel: laktattrappa (5 min, 25 W, prov utan paus), ramptest (20 W per minut) och FTP 20 minuter (ett steg utan effektmål, `Stage.free`). SkiErg: laktattrappa (4 min, 15 W, 30 s paus) och ramptest. Nivåerna och stegen är programmets egna förslag | `assessments/library_power.py`, se [tester-och-zoner.md](tester-och-zoner.md) |
| Testkörningen | Tre slags ergometer: styrd (trainer i ERG), bara mätt (PM5: TV:n visar målet, stegklockan går själv, stegets belastning är uppmätt medeleffekt) och manuell. `stage_stats` har mål, uppmätt medel och kadens per steg, och sparas i `result["stage_stats"]`. En ramp slutar av sig själv när idrottaren slutar trampa eller staka | `assessments/runner.py`, `ui/live_controller.py` |
| Trösklarna i watt | Kurvanpassningen går på en normerad axel (`_Model.sx`), toleranserna är andelar av belastningen, steg med nästan samma uppmätta effekt blir en punkt, och lutningsvarningen gäller bara löpning. Löpningen räknas som förut | `assessments/lactate.py` |
| Zoner, jämförelse, maxpuls | Zongränsen bortom testet och bruset i jämförelsen kommer ur profilen. Jämförelsen skriver watt och tempo per 500 m. Ålderskriteriet för maxpuls bedöms bara i löpning | `zone_systems.py`, `compare.py`, `max_hr.py` |
| FTP och effekt | FTP ur 20 minuter (95 % av bästa 20 min) och ur ramp (75 % av bästa minuten), bästa minuteffekt, W/kg och Coggans sju zoner | `assessments/power.py` |
| Maxpuls per sport | `Athlete.max_hr` är löpningens maxpuls. De andra ligger i `Athlete.sport_max_hr`, och `max_hr_for(sport)` ger rätt värde. Maxpulsen lånas aldrig från en annan sport. Idrottarens sida har fälten *Maxpuls cykel* och *Maxpuls SkiErg* | `core/models.py`, `storage/db.py`, `ui/views/athletes.py`, `results.py` |
| Trösklar per sport | `services.sport_thresholds(store, idrottare, sport)` räknar dem ur det senaste laktattestet i sporten | `ui/services.py` |
| Databasen | Version 8, se [avsnitt 3](#3-databasmigrering). Version 9 är träningsschemat, se [TRANINGSPLAN.md](../TRANINGSPLAN.md). Cykel och SkiErg behövde ingen ny migrering: vikten på testdagen, stegens mätvärden och FTP ligger i testets `result_json` | `storage/db.py` |
| Guiden | Sporten väljs först i steget *Testtyp* och styr testerna (`KINDS_BY_SPORT`), protokollens enhet, enhetssteget (trainer eller PM5) och listan *Innan start*. Kroppsvikten frågas efter för cykel när den saknas | `ui/views/new_test.py` |
| Livevyn och TV:n | Rutorna följer sporten: watt, mål och kadens för cykel, watt, tempo per 500 m och takt för SkiErg, och effekten över tid under meddelanderutan. Varning vid låg kadens i ERG och när åkaren ligger utanför målet | `ui/views/live_board.py`, `operator.py` |
| Resultatet | Trösklar i watt med W/kg eller tempo, tabellen med uppmätt effekt och kadens, kortet *Effekt och FTP*, zoner med effekt. Historiken skriver sporten först, och jämförelsen och den samlade rapporten tar en sport i taget | `ui/views/results.py`, `history.py`, `ui/services.py` |
| Rapporten | Sporten sätts en gång per rapport (`_SPORT`), och `ts()` väljer sportens text. Kolumnen bredvid belastningen är tempo per km, tempo per 500 m eller W/kg. Eget avsnitt *Effekt* med FTP och effektzoner | `report/builder.py`, `charts.py`, mallen |

**Vägen för ny kod:** läs belastningen som `load` (`StepRecord.load`, `TimeSample.load`, `Stage.load`, `Protocol.increment_load`, `RunnerState.load`, `Threshold.load`, `Zone.load_low` och `load_high`, `ErgometerState.load`) och ta enheten ur `profile(sport)`. Fälten bakom heter fortfarande `speed_kmh`, och `load` går bara att läsa. Den som bygger en `Stage` eller en `StepRecord` skriver alltså `speed_kmh=` tills namnbytet är gjort.

**Det som återstår:**

| Del | Kvar |
|---|---|
| Hårdvaran | Inget i cykel- och SkiErg-koden har mött en riktig trainer eller en PM5. PM5-paketens fältordning är skriven ur minnet och ska kontrolleras först, se [PROV.md](../PROV.md#skiergen) |
| 0a | Guldtestet med sparad resultat-JSON och rapport-HTML. Rapporten för löpning jämfördes i stället för hand före och efter varje steg: samma HTML |
| 0b | Namnbytet `speed_kmh` → `load` i fälten, i kolumnen `step_records.speed_kmh` och i JSON-nycklarna. `GAIT` → `TECHNIQUE`, `treadmill_mode` → `ergometer_mode` |
| 0c | `FtmsBike` ärver `FtmsTreadmill` för att få kontrollpunkten utan att `ftms.py` rörs. Det delade ska flyttas till en egen bas när bandet är provat. Ämnet på bussen heter fortfarande `treadmill` (`TOPIC_ERGOMETER` är samma ämne) |
| Uppskattade zoner mellan sporter | Inte byggt, se nedan |
| Manuellt läge i watt | Medeleffekten skrivs in i resultatet efter testet, inte på operatörssidan under det. FTP och bästa minuten räknas fortfarande bara ur det som mättes |
| SkiErg | Dragfaktorn sparas och visas, men programmet varnar inte när den skiljer från förra testet. Kraftkurvan (0x003D) läses inte. Passet som läggs in på monitorn (CSAFE) är oprovat, och monitorns klocka synkas inte med testets efter starten |
| Cykel | Trainer utan FTMS (Cycling Power, bara läsning) och QZ-vägen är inte byggda. Startwatten föreslås ur nivå, inte ur idrottarens förra test |
| Inställningar | Förvalt enhetsläge finns bara för löpbandet |

**Det som medvetet inte är gjort:**

- **Uppskattade zoner för en sport utan eget test.** En uppskattning är bättre än inget, med villkoret att avdragen per sport beläggs med källa. Källkontrollen ([tester-och-zoner.md](tester-och-zoner.md#kontroll-mot-källa)) kunde inte belägga dem: studien bakom siffrorna för cykel gick inte att hitta, och Roecker m.fl. 2003 fann att tröskelpulsen på cykel och i löpning inte ens samvarierar. Programmet säger därför i resultatet och rapporten att sporten saknar eget test, och räknar inte om något.
- **Teknikanalys i de här testerna.** Cykelns och SkiErgens trösklar och ramper filmas inte. Bikefit och stakteknik är egna tester som filmas, enligt regeln att ett test görs för det som det mäter, se [Bikefit och stakteknik](bikefit-och-stakteknik.md#nuläget-i-koden).
- **Den samlade rapporten över flera sporter.** Den tar tester i en sport åt gången.

## 1. Var löpningen är hårdkodad

### core

| Var | Löpspecifikt | Bör bli |
|---|---|---|
| `core/models.py:4` | Modulens enheter: "speeds in km/h; incline in percent" | **Gjort:** modulens text säger att belastningen följer sporten |
| `core/models.py:27` `Athlete.height_cm` | Kommentaren säger gait, annars allmän | Oförändrad |
| `core/models.py:29` `Athlete.max_hr` | En maxpuls per idrottare. Maxpulsen skiljer mellan löpning, cykel och stakning | **Gjort:** `max_hr` är löpningens värde, `sport_max_hr` bär de andra sporterna och `max_hr_for(sport)` läser rätt värde. Lagras i `athlete_sport` |
| `core/models.py:39` `TestKind` | `GAIT`, `LACTATE_STEP`, `MAX_HR` | Samma värden, och `FTP` (cykelns 20-minuterstest) har tillkommit. Kvar: `GAIT` byter namn till `TECHNIQUE`, värdet `"gait"` behålls i databasen |
| `core/models.py:47` `Session` | Ingen sport, `treadmill_mode` | **Gjort:** `sport: Sport`. Kvar: `treadmill_mode` → `ergometer_mode` |
| `core/models.py:65` `StepRecord` | `speed_kmh`, `incline_pct`, `gait` | **Delvis:** egenskapen `load` finns. Kvar: fälten byter namn till `load` och `technique` |
| `core/models.py:79` `TimeSample` | `speed_kmh`, `incline_pct`, `cadence_spm` | **Delvis:** egenskapen `load` finns. Kvar: fälten `load` och `cadence` (enheten ur profilen) |
| `core/bus.py:14` `TOPIC_TREADMILL` | Ämnet och lasten `TreadmillState` | **Delvis:** `TOPIC_ERGOMETER` finns som samma ämne under nytt namn, och `TOPIC_STROKE` bär SkiErgens drag. Kvar: ämnets värde heter `treadmill` |
| `core/bus.py:16–17` `TOPIC_GAIT_STEP`, `TOPIC_GAIT_SUMMARY` | Ett meddelande per markkontakt | Behålls till steg 4, då behovet är känt |

### assessments

| Var | Löpspecifikt | Bör bli |
|---|---|---|
| `protocol.py:15` `Stage` | `speed_kmh`, `incline_pct`, `record_gait` | **Delvis:** egenskapen `load` finns, och `free` (steg utan satt belastning). Kvar: fälten `load` och `record_technique` |
| `protocol.py:26` `Protocol` | `increment_speed_kmh`, `increment_incline_pct`, `final_sprint_kmh`, ingen sport | **Delvis:** `sport` finns (ett protokoll utan sport läses som löpning), och egenskapen `increment_load`. Kvar: fälten `increment_load` och `final_sprint_load`, och att `from_dict` läser båda namnen |
| `protocol.py:65` `RunnerState` | `speed_kmh`, `incline_pct`, prompten "Sätt bandet på …" | **Delvis:** egenskapen `load`, och för watt-sporterna `sport`, `target_load`, `cadence`, `self_paced`, `off_target` och `low_cadence`. Kvar: fältet |
| `library.py:28` `TreadmillLimits` | Gränser i km/h och % | **Delvis:** samma klass bär wattens gränser (`library_power.power_limits`: farten är watten, ingen lutning). Kvar: namnet |
| `library.py:55` LT2-farter per nivå, `start_speed_for` | Startfart ur löparens nivå | **Gjort:** `LEVELS_BIKE`, `LEVELS_SKIERG` och `start_power_for` i `library_power.py` |
| `library.py:86–232` `lactate_step`, `max_hr_ramp`, `lactate_to_max`, `gait_check` | Alla byggare i km/h med 1 % lutning | **Gjort:** byggarna är löpningens bibliotek. Cykel och SkiErg har sina i `library_power.py` (en fil för båda, eftersom de delar allt utom nivåer och texter) |
| `library.py:245` `default_protocols` | En lista | **Gjort:** `default_protocols(limits, sport)` ger sportens bibliotek |
| `runner.py:85` `stage_text` | "12,0 km/h · 1 %" | **Gjort:** `stage_text(load, incline, sport)` ger "200 W" och "202 W · 2:00 /500 m". Löpningens text är oförändrad |
| `runner.py:98` `cooldown_kmh=5.0` | Nedvarvning i gångfart | **Gjort:** nedvarvningens belastning ur profilen (80 W på trainern, ingen på SkiErg) |
| `runner.py:594–614` `_set_belt`, `_belt_stop` | Kallar `set_target(speed, incline)` | **Delvis:** samma metoder sätter watt, släpper trainern och ger en PM5 målet. Kvar: namnen |
| `runner.py:205, 647–673` | Läser `st.speed_kmh` och `cadence_spm` | **Delvis:** kadensen läses ur ergometern i watt-sporterna. Kvar: `st.load` och `cadence` som fältnamn |
| `lactate.py:48` `pace_str`, `:57` `Threshold` | `speed_kmh`, `pace_min_per_km`, `incline_pct` | **Delvis:** `Threshold.sport` och `LactateResult.sport` finns, `Threshold.load` och tempot ur profilen. Kvar: fältens namn |
| `lactate.py:351` `MIN_THRESHOLD_GAP = 0.05` | Minsta avstånd mellan trösklarna, i km/h | Oförändrad: gränsen är en andel (5 % av LT2) och gäller watt lika väl. Avståndet 0,05 km/h vid valet av LT1 är en andel av belastningen för watt |
| `lactate.py:387` varningen `incline_varies` | Bara löpning | **Gjort:** hoppas över när sporten saknar lutning |
| `zones.py:41` `Zone` | `speed_low_kmh`, `speed_high_kmh`, `pace_low`, `pace_high` | **Delvis:** `Zone.sport`, `load_low`, `load_high` och tempot ur profilen. `Anchors.sport` säger vilken sports maxpuls zonerna gäller. Kvar: fältens namn |
| `zones.py:127` `v2 * 1.05` | "5 % över LT2-farten" | **Gjort:** `SportProfile.lt2_overshoot`: 1,05 för löpning, 1,06 för cykel (Coggans gräns mellan tröskel och VO2max) och 1,10 för SkiErg (programmets eget val, ej belagt) |
| `max_hr.py:48, 139` | `speed_kmh`, `incline_pct` per steg | Kvar: `load` i `stage_hr`. Ålderskriteriet bedöms bara i löpning |
| `compare.py:23–40, 88` | `speed_delta`, `pace_delta_s`, `curve_shift_kmh`, `RIGHT_SHIFT_KMH` | **Delvis:** bruset i förskjutningen ur profilen, och sammanfattningen i watt och tempo per 500 m. Kvar: fältnamnen |

### devices

| Var | Löpspecifikt | Bör bli |
|---|---|---|
| `base.py:30` `DeviceStatus.device` | `"treadmill"` eller `"heart_rate"` | Kvar: ergometerns plats heter fortfarande `"treadmill"`, också för trainern och SkiErgen |
| `base.py:45` `TreadmillState` | `speed_kmh`, `incline_pct`, mål för båda | **Gjort:** `TreadmillState` har egenskaperna `load` och `target_load`, och `ErgometerState` ärver den med effekt, kadens, tempo per 500 m och dragtider |
| `base.py:64` `Treadmill` | `min_speed_kmh`, `max_speed_kmh`, `max_incline_pct`, `set_target(speed_kmh, incline_pct)` | **Gjort:** `Ergometer` (Protocol) med `sport`, `controllable`, `measured`, gränserna, `set_target(load, incline)`, `start`, `stop` och `state` |
| `ftms.py` hela filen | Treadmill Data 0x2ACD, fart- och lutningsintervall, Set Target Speed | Orörd. Cykeln har `ftms_bike.py`, där `FtmsBike` ärver `FtmsTreadmill` för kontrollpunkten. Kvar: en gemensam bas när bandet är provat |
| `qz.py:102` `QzTreadmill` | Läser fart och lutning ur QZ | Orörd. Ingen QZ-väg för cykel (Zwift och QZ stängs under ett test) |
| `manual.py:19`, `sim.py:81` | `ManualTreadmill`, `SimTreadmill` | **Gjort:** `ManualErgometer`, `SimBike` och `SimSkiErg` |
| `sim.py:5` pulsmodellen | ACSM:s löpekvation (fart och lutning) | **Gjort:** med en trainer eller en SkiErg följer pulsen effekten |
| `manager.py:35–149` | `TreadmillMode`, `connect_treadmill`, `scan_treadmills`, tjänsten 0x1826 | **Gjort:** `connect_ergometer(sport, mode)` och `scan_ergometers(sport)`. Löpbandets metoder är kvar |

### pose

| Var | Löpspecifikt | Bör bli |
|---|---|---|
| `types.py:48` `StepMetrics`, `:79` `GaitSummary`, `:177` `GaitAnalysis` | Kontakttid, översteg, fotisättning, `speed_kmh` | Löpningens profil. `Pose`, `Finding`, `Severity`, `KeyFrame` och `View` är allmänna och delas |
| `gait.py` | Markkontakter ur fotens framsving och höjd över bandet | Löpningens cykelhändelse. Cykel: pedalvarv ur fotledens bana. SkiErg: drag ur handledens höjd |
| `metrics.py` | Mått per steg, asymmetri vänster och höger | Löpningens mått. Bikefit: vinklar i pedalens lägen. Stakning: vinklar i dragets faser |
| `findings.py:63–206` | Riktvärden ur löplitteraturen, beroende av fart och längd | Löpningens riktvärden. Nya filer med egna källor per sport |
| `offline.py:120` `_KIND_OF`, `:150` `plan_keyframes` | Nyckelbildernas slag (isättning, mittstöd, frånskjut) | Nyckelbildsplan per profil |
| `annotate.py:475, 586` | Ritar löpningens vinklar | Ritfunktioner per profil. `Canvas`, skelettet och bildtexten delas |
| `live.py:98, 136` | Hämtar bandets fart ur `TOPIC_TREADMILL` för riktvärdena | Hämtar `load` ur `TOPIC_ERGOMETER`. Profilen väljer analys |
| `synthetic.py` | Syntetisk löpare för testerna | Behålls. En syntetisk cyklist och stakare behövs för steg 4 och 5 |
| `estimator.py`, `filters.py`, `series.py`, `models.py` | Inget | Delas av alla sporter |

### storage

| Var | Löpspecifikt | Bör bli |
|---|---|---|
| `db.py:57` `sessions` | Ingen sport, `treadmill_mode` | **Gjort:** `sport` och indexet `(athlete_id, sport, kind, started)`. Kvar: `ergometer_mode` |
| `db.py:72` `step_records` | `speed_kmh`, `incline_pct`, `gait_json` | `load`, `incline_pct`, `technique_json` |
| `db.py:190` `pack_samples` | Kolumnerna `v`, `inc`, `cad` | Samma nycklar, `v` betyder belastning. Ingen ändring i lagrade data |
| `db.py:95` `gait_analyses`, `:103` `keyframes` | Namnet | Behålls. Innehållet är JSON och profilen följer testets sport |
| `db.py:460–477` `sessions`, `previous_session` | "Förra testet" väljs på idrottare och testtyp | **Gjort:** `sessions(…, sport)` och `previous_session` väljer också på sport |
| `db.py:45` `athletes.max_hr` | En maxpuls | **Gjort:** `athlete_sport`, se avsnitt 3 |

### report

| Var | Löpspecifikt | Bör bli |
|---|---|---|
| `builder.py:106` `sections()`, `:124` | Avsnitten `lactate`, `max_hr`, `gait` | Samma tre, där teknikavsnittet väljer mall efter profilen |
| `builder.py:144` `MetricSpec`-listan | Löpteknikens mått och skalor | En lista per profil |
| `builder.py:172, 181, 231` `pace_str`, `_pace_of`, `_pace_range` | min/km | **Gjort:** tempo per km, tempo per 500 m eller W/kg efter rapportens sport |
| `builder.py:468–533` `_lactate_ctx`, `_zones_ctx` | Kolumnerna fart, tempo och lutning | **Gjort:** kolumnerna följer sporten, och lutningen visas bara för löpning |
| `builder.py:624–642` | Tabellen "per fart" | "Per belastning" |
| `builder.py:689, 716` | `"km/h"` i trend och sammanfattning | **Gjort:** enheten ur profilen |
| `charts.py:133, 233, 238` | `Threshold.speed_kmh`, "km/h" i etiketten, `report.chart.speed_axis` | **Gjort:** enheten, decimalerna och axelns text kommer från rapporten |
| `charts.py:250` `hr_chart` | Fartbandet längst ner | **Gjort:** bandet längst ner visar effekt i watt-sporterna |
| `report.html.j2:157, 292` | `km/h` skrivet direkt i mallen | **Gjort:** `load_unit` ur sammanhanget |
| `report.html.j2:26–27, 52–53, 178–179` | Kolumnerna tempo, fart och lutning | **Gjort** |
| `report.html.j2:229–341` | Tre löpteknikavsnitt | Flyttas till en delmall per profil i steg 4 |

### ui

| Var | Löpspecifikt | Bör bli |
|---|---|---|
| `fmt.py:14` `pace` | min/km | **Gjort:** `pace(load, sport)`, `load(v, sport)` och `load_unit(sport)`. Vyerna använder dem inte än (steg 0d) |
| `context.py:27, 45, 108` | `devices.treadmill_mode`, `TestPlan.treadmill_mode` | **Delvis:** `TestPlan.sport` följer med till testet, och vikten sparas på testet i watt-sporterna. Kvar: förvalt läge per sport som inställning |
| `services.py:126–165` | Byggarna i km/h | **Gjort:** `sports()`, `default_protocols(sport)` och byggarna för watt |
| `services.py:190–253` | `Threshold.speed_kmh`, zonernas `speed_low` och `speed_high` | **Delvis:** analysen och zonerna tar `sport`, och `sport_thresholds` ger idrottarens trösklar i en sport. Kvar: `load` i vyernas dataklasser |
| `live_controller.py:34, 115, 144` | `update_gait_targets(speed_kmh)`, `treadmill_changed`, zonerna ur `athlete.max_hr` | **Delvis:** zonerna på TV:n räknas ur sportens maxpuls, och stegens mätvärden sparas. Kvar: `ergometer_changed` |
| `views/new_test.py` guidens steg (sex när testet filmas, annars fem) | Steget *Testtyp* har löpningens tester och kombinationerna | **Gjort:** sportvalet ligger först i steget *Testtyp*. Raden visas först när det finns mer än en sport att välja |
| `views/new_test.py:226–262, 303–375` | Fälten startfart, ökning och lutning i km/h och % | **Gjort:** fälten och tabellen följer profilen (watt, ingen lutning, tempo per 500 m) |
| `views/new_test.py:533` enhetssteget | Lägena FTMS, QZ och manuellt för bandet | **Gjort:** lägena för sportens ergometer |
| `views/live_board.py:151, 198–201` | Brickorna puls, fart, lutning, kadens och löpteknikens brickor | **Gjort:** rutorna följer sporten |
| `views/live_board.py:305–404` | "km/h", lutningsraden, tempot under farten | **Gjort** för watt-sporterna. Löpningens texter är oförändrade |
| `views/operator.py:169–223` | "Farten är satt till …" | **Gjort:** texterna följer sporten |
| `views/results.py:83–84, 126, 369, 445` | Brickorna LT1 och LT2 i km/h, tabellhuvudet, zonernas fartspann | **Gjort:** enheten ur profilen |
| `views/history.py:20, 160, 210` | Ikon per testtyp, "LT2 … km/h", jämförelsens kolumner | **Delvis:** sporten står före testtypen, enheten ur profilen, och jämförelsen tar en sport. Kvar: filter per sport |
| `views/settings.py:172, 325` | Förvalt bandläge, CSV-huvudet "km/h, lutning" | Per sport. CSV:n får kolumnerna sport, belastning och enhet |
| `main_window.py:103, 138` | Statusbrickan "Band" | **Gjort:** statusbrickan heter efter den anslutna ergometern |
| `widgets/charts.py:176` | `x_unit = "km/h"` | **Gjort:** vyn sätter `x_unit` |
| `demo.py` | Syntetisk löpare, `TestKind.LACTATE_STEP` på band | Demot förblir löpning tills vidare |

### i18n

| Var | Löpspecifikt | Bör bli |
|---|---|---|
| `ui.*.json` (cirka 60 svenska texter) | "Band", "Fart", "Lutning", "Löpteknik", "Sätt bandet på …", `ui.kind.gait` | **Gjort:** texterna för watt-sporterna har sporten i nyckeln (`ui.mode.bike.ftms`, `ui.kind.skierg.ramp`). Löpningens texter är oförändrade |
| `assessments.*.json` | Protokollens namn och beskrivningar, zonernas syfte ("lugn jogg") | **Gjort:** protokolltexter och uppmaningar per sport (`assessments.prompt.bike.…`) |
| `report.*.json:64, 143, 152, 153` | `zones.note` (löpband med 1 % lutning), `chart.speed_axis`, `col.speed`, `col.pace` | **Gjort:** sportens text väljs med nyckeln plus sporten (`report.col.speed.bike`) |
| `pose.*.json`, `devices.*.json` | Fynd, råd och övningar för löpning. Bandets felmeddelanden | Nya nycklar per profil och ergometer |
| `ui.app.name` | "Löpanalys" | Ett öppet beslut, se avsnitt 5 |

### tester

| Var | Löpspecifikt | Bör bli |
|---|---|---|
| `tests/assessments/*` (6 filer), `tests/report/*` (2), `tests/storage/test_db.py`, `tests/ui/test_services.py` | Bygger `Stage` och `StepRecord` med `speed_kmh` | Följer namnbytet i steg 0b. Påståendena ändras inte |
| `tests/devices/*` (4 filer) | `TOPIC_TREADMILL`, `TreadmillState` | Bara ämnets namn ändras |
| `tests/pose/*` | Syntetisk löpare | Orörda till steg 4 |
| Saknas | Ett test som låser löpningens utdata | **Delvis:** `tests/core/test_sport.py` och `tests/storage/test_sport_migration.py` låser sporten och migreringen. Guldtestet med sparad rapport-HTML är inte lagt: rapporten jämfördes för hand före och efter steg 0 (se [Nuläget](#nuläget)) |

## 2. Målbild

**Sporten är ett värde på testet, och en profil säger vad sporten betyder.** Ingen arvshierarki och inga insticksmoduler: en enum, en frusen dataklass och en uppslagstabell i `core/sport.py`.

```python
class Sport(str, Enum):
    RUN = "run"; BIKE = "bike"; SKIERG = "skierg"

@dataclass(frozen=True)
class SportProfile:
    sport: Sport
    load_unit: str          # "km/h" | "W"
    load_decimals: int      # 1 | 0
    has_incline: bool       # bara löpning
    pace: Callable | None   # belastning -> "5:00" (min/km), "2:05" (/500 m), None för cykel
    ergometer: str          # "treadmill" | "bike" | "skierg"
    cycle: str              # "step" | "pedal_rev" | "stroke"
    cadence_unit: str       # "spm" | "rpm" | "spm" (drag/min)
    live_tiles: tuple[str, ...]
```

| Sporten styr | Löpning | Cykel | SkiErg |
|---|---|---|---|
| Belastning | Fart, km/h, plus lutning % | Effekt, W (och W/kg i rapporten) | Effekt, W. Tempot per 500 m visas bredvid |
| Ergometer | Löpband: FTMS, QZ, manuellt | Trainer: FTMS Indoor Bike i ERG-läge, manuellt | Concept2 PM5: läses, går inte att styra. Körs som manuellt läge: skärmen visar målet och idrottaren håller det |
| Protokoll | De som finns i dag | Laktattrappa (till exempel 100 W + 30 W per 4 min), maxpulsramp (+20 W/min), bikefit | Laktattrappa (steg i W eller tempo), maxpulsramp, stakteknik |
| Zoner | Puls, fart och tempo | Puls och watt | Puls, watt och tempo per 500 m |
| Analysprofil | Steg: kadens, översteg, kontakttid, höftfall | Pedalvarv: knävinkel i nedre läget, höft- och bålvinkel, knäets spår framifrån | Drag: bålens fällning, knäböjning, armvinkel i fattningen, draglängd, förhållandet drag och återföring |
| Rapport | Laktat, zoner, maxpuls, löpteknik | Laktat, zoner, maxpuls, bikefit | Laktat, zoner, maxpuls, stakteknik |
| Livebrickor | Puls, fart, lutning, kadens | Puls, watt, kadens (varv/min) | Puls, watt, tempo per 500 m, takt (drag/min) |

**Hur det hålls enkelt:**

1. **Ett tal för belastningen.** `Stage`, `StepRecord`, `TimeSample`, `Threshold` och `Zone` får `load`. Enheten står inte i varje rad, den följer testets sport. `incline_pct` ligger kvar som ett fält som är 0 utanför löpning. Laktatkurvan, trösklarna och jämförelsen räknar redan på ett x-värde utan enhet.
2. **Enheterna behåller sina klasser.** `FtmsTreadmill` och `TreadmillState` är verkligen ett löpband och byter inte namn. Det gemensamma är bara `set_target(load, incline)` och egenskapen `state.load`. Varje ny ergometer blir en ny fil.
3. **Analysprofilen är en modul per sport** med samma fem funktioner: `detect_events`, `cycle_metrics`, `summarize`, `evaluate` och `plan_keyframes`. Löpningens moduler (`gait.py`, `metrics.py`, `findings.py`) flyttas inte, de registreras bara som profilen för `Sport.RUN`.
4. **`TestKind` fick två värden för tekniktesterna:** `BIKEFIT` (`bikefit`) för cykel och `SKI_TECHNIQUE` (`ski`) för SkiErg, samma namn som analysen använder. Löpningens `GAIT` ligger kvar.
5. **Zonerna räknas fram, de lagras inte.** De kommer ur idrottarens senaste laktattest i sporten och sportens maxpuls, precis som i dag. Det enda nya som lagras är maxpulsen per sport.

## 3. Databasmigrering

Sporten kom i version 8 av databasen (`storage/db.py`). **Migreringen lägger bara till,** inget döps om, flyttas eller skrivs om:

| Tabell | Ändring | Befintliga rader |
|---|---|---|
| `sessions` | Ny kolumn `sport TEXT NOT NULL DEFAULT 'run'` och ett nytt index `(athlete_id, sport, kind, started)` | Alla blir löptester |
| `athlete_sport` (ny) | `athlete_id`, `sport`, `max_hr`, `updated`, primärnyckel `(athlete_id, sport)` | `athletes.max_hr` kopieras till raden för `run` |
| `athletes` | Ingen | `max_hr` ligger kvar och är löpningens maxpuls. Raden för `run` i `athlete_sport` följer den varje gång idrottaren sparas |
| `step_records` | Ingen | `speed_kmh` är belastningen i testets sport, alltså km/h för alla befintliga rader |
| `time_samples`, `gait_analyses`, `keyframes`, `lactate_entries`, `settings` | Ingen | Nycklarna `v`, `inc` och `cad` behålls |

- **Säkerhetskopian:** innan en befintlig databas migreras sparas en kopia bredvid den, `lopanalys.sqlite.v<gammal version>.bak`. Det gällde redan före sporten.
- **JSON i raderna migreras inte.** `protocol_json` och `result_json` i gamla tester saknar `sport`, och det läses som löpning (`core.sport.sport_of`). Nya tester skriver `sport` i protokollet, i laktatresultatet och i zonerna.
- **Ett äldre program öppnar inte en migrerad databas.** Det vägrar en databas med nyare version och rör den inte. Vägen tillbaka är kopian `.bak`.
- **Versionsnumret delas med allt annat som ändrar databasen.** Medan steg 0 byggdes tog huvudgrenen version 3 (testets status) och 4–7 (dataskydd, samtycke, e-post och klockans VO2max). Sportens migrering lades därför sist, som version 8, när grenen flyttades till huvudgrenen 2026-11-14. En databas som öppnats med förhandsversionen av sporten (då version 4) ska inte användas vidare.
- **Skyddet:** `tests/storage/test_sport_migration.py` bygger en databas i version 2 (som `dev-003ea3d` skrev den) och en i version 3 (som huvudgrenen skrev den när steg 0 gjordes), med två laktattester och ett tekniktest. Provet visar att varje rad läses lika kolumn för kolumn efter migreringen, att alla tester är löptester, att trösklarna och rapporten ger samma siffror, att kopian finns kvar i sin gamla version och att *förra testet* aldrig hämtas ur en annan sport. Ett eget prov bygger en databas i version 7, som huvudgrenen skrev den före sporten, och visar att den blir version 8 med samtycke, e-post och klockans VO2max kvar.

**Kvar till namnbytet (0b):** kolumnerna `speed_kmh` → `load`, `gait_json` → `technique_json` och `treadmill_mode` → `ergometer_mode` med `RENAME COLUMN` (SQLite 3.25 eller nyare), och JSON-nycklarna `speed_kmh`, `increment_speed_kmh` och `speed_low_kmh`, som då måste gå att läsa under båda namnen för alltid.

## 4. Stegordning

Löpningen fungerar oförändrat efter varje steg. Vad som är gjort står i [Nuläget](#nuläget).

| Steg | Innehåll | Storlek | Filer (huvuddrag) |
|---|---|---|---|
| **0a** | **Gjort**, utom guldtestet: `core/sport.py` med profiler för alla tre sporterna, `sport` på `Session`, `Protocol` och `TestPlan`, migrering 8, `athlete_sport`, sport i `previous_session` | S | `core/`, `storage/db.py`, `assessments/protocol.py`, `ui/context.py`, 2 testfiler |
| **0b** | **Delvis:** egenskapen `load` finns bredvid `speed_kmh`. Kvar: namnbytet i fälten, kolumnerna och JSON-nycklarna, med läsning av gamla nycklar. `GAIT` → `TECHNIQUE` | M–L | `core/models.py`, `assessments/*` (7), `storage/db.py`, `report/*` (3), `ui/services.py`, vyerna, cirka 14 testfiler |
| **0c** | **Kvar:** `Ergometer`-gränssnittet, `state.load`, `TOPIC_ERGOMETER`, `connect_ergometer` | S–M | `core/bus.py`, `devices/*` (6), `runner.py`, `pose/live.py`, `ui/bridge.py`, `live_controller.py`, 5 testfiler |
| **0d** | **Delvis:** sportvalet finns i guiden, rapportens enhet och TV-texten kommer ur profilen, och `fmt` har formateringen. Kvar: brickorna, guidens fält, diagramaxlarna, rapportens kolumner och språkfilerna | M | `ui/fmt.py`, `ui/views/*` (6), `ui/widgets/charts.py`, `report/builder.py`, `charts.py`, mallen, 4 språkfiler |
| **1a** | **Gjort.** Cykelns enheter: `ftms_bike.py`, `SimBike`, `ManualBike`, pulsmodell ur watt | M | Nya filer i `devices/`, `manager.py` |
| **1b** | **Gjort.** Cykelns protokoll, zoner i watt, gränserna i `lactate.py` och `zones.py` per sport | M | `assessments/library_bike.py` (ny), `zones.py`, `compare.py` |
| **1c** | **Gjort.** Cykel i guiden, livevyn, resultatet och rapporten. W/kg | M | `ui/views/*`, `report/*`, språkfilerna |
| **2a** | **Gjort.** SkiErg: PM5 över Bluetooth (läsning), `SimSkiErg`, tempo per 500 m ur watt | M | Nya filer i `devices/` |
| **2b** | **Gjort.** SkiErgens protokoll och zoner | S | `assessments/library_skierg.py` (ny) |
| **2c** | **Gjort.** SkiErg i guiden, livevyn och rapporten | S–M | Samma filer som 1c |
| **3** | Analysprofilen som gränssnitt, löpningen registrerad som profil, delmall per profil i rapporten | M | `pose/offline.py`, `live.py`, `annotate.py`, `report/builder.py`, mallen |
| **4** | Bikefit: pedalvarv, vinklar, riktvärden, nyckelbilder, syntetisk cyklist, rapportavsnitt | L | Nya filer i `pose/`, delmall, språkfiler |
| **5** | Stakteknik: drag, faser, riktvärden, nyckelbilder, syntetisk stakare, rapportavsnitt | L | Nya filer i `pose/`, delmall, språkfiler |

**Vad som kan göras parallellt:**

- **Resten av steg 0 görs i ordning av en agent i taget.** 0b, 0c och 0d rör samma filer (`runner.py`, vyerna, `builder.py`).
- **Efter steg 0:** 1a (devices), 1b (assessments) och steg 3 (pose och teknikdelen av rapporten) rör olika mappar och kan göras av tre agenter samtidigt. 2a kan gå parallellt med 1a, eftersom varje ergometer är en egen fil. Bara `manager.py` delas, och den tas av den som blir klar sist.
- **1c och 2c görs efter varandra**, de rör samma vyer och samma mall.
- **Steg 4 och 5 kan göras parallellt** efter steg 3, eftersom varje profil ligger i egna filer. Språkfilerna är den enda krockytan: varje agent lägger sina nycklar i ett eget block sist i filen, eller så får profilerna egna filer (`pose_bike.sv.json`).

## 5. Risker och det som bör vänta

**Det som är oprovat enligt [PROV.md](../PROV.md) och kan ändra grunden:**

| Oprovat | Vad provet kan ändra | Följd för ombyggnaden |
|---|---|---|
| Bandet över FTMS (anslutning, mål, säkerhetsnyckel, återanslutning) | `ftms.py`, `_runtime.py` och hur `runner.py` faller tillbaka till manuellt läge | Rör inte `ftms.py`. Vänta med 0c och med att dela kod till `ftms_bike.py` tills bandet är provat |
| QZ (fart, lutning, sträckans enhet) | `qz.py`, och frågan om QZ ska bära cykeln också | Avgör QZ för cykel först efter provet |
| Laktattrappan på bandet, manuellt läge, maxpulstrappan | Faserna och tiderna i `runner.py`, pausen för provet, promptarna | Vänta med 0b. Det är den fil som namnbytet går djupast i |
| Steghändelser mot Garmin (kadens ±2, kontakttid ±15 ms), bakifrån, videoanalysen | `gait.py`, `metrics.py`, filtren, trösklarna | Vänta med steg 3. Gränssnittet för analysprofilen bör formas efter en löpanalys som stämmer |
| GoPro live (fördröjningen), 120 bilder/s | Kamerans trådar och analysens takt i `live.py` | Bikefit och stakteknik kräver samma kedja, så vänta med steg 4 och 5 |
| Utskrift och PDF på Windows, riktiga bilder | Mallens sidbrytningar och CSS | Vänta med delmallarna i steg 3 |
| Livevyn på TV:n (läsbart på 3 m) | Brickornas storlek och antal | SkiErgen behöver fyra brickor plus teknikbrickor: provet avgör om det ryms |

**Därför stannade steg 0 där det gjorde:** det som är gjort lägger till (en kolumn med förvalt värde, en tabell, egenskaper och parametrar med löpning som förval). I de provkänsliga filerna är bara texten på TV:n i `runner.py` rörd, och `ftms.py`, `qz.py`, `gait.py` och kamerakoden inte alls.

**Övriga risker:**

1. **Namnbytet är stort och ger ingen funktion.** Cirka 50 filer, och ett fel syns först i en rapport eller i en gammal databas. Ett guldtest och migreringstestet är skyddet, och guldtestet ska läggas före namnbytet. Alternativet att behålla `speed_kmh` och lägga `power_w` bredvid ger villkor i laktat, zoner, jämförelse, rapport och varje vy, och avråds.
2. **Gamla tester i databasen.** JSON med gamla nycklar måste gå att läsa för alltid, annars försvinner historiken och jämförelsen med förra testet.
3. **Konstanter som ser allmänna ut men är i km/h:** `MIN_THRESHOLD_GAP`, `RIGHT_SHIFT_KMH`, `v2 * 1.05` och avrundningen av zongränser. De ger fel zoner i watt utan att något test larmar. Varje konstant flyttas till profilen i steg 1b med ett eget test.
4. **Concept2 PM5** går inte att styra, och vilken Bluetooth-tjänst den erbjuder (Concept2:s egen eller FTMS) beror på enhetens programvara. Det ska kontrolleras mot en riktig SkiErg innan 2a storleksbestäms.
5. **SkiErgens belastning:** watt och tempo hänger ihop i kubik (Concept2: W = 2,80 / (s/m)³). Trappan bör ha steg i watt, annars blir stegen ojämna. Coacher tänker ofta i tempo, så guiden bör visa båda.
6. **Bikefit och stakteknik saknar riktvärden i koden** och kräver egna källor, som löpningen har. Det är sakarbete, inte bara kod, och det är därför stegen är L.
7. **Överabstraktion.** En tredje ergometer eller en fjärde sport ska inte förberedas. Profilen får bara de fält som två sporter faktiskt skiljer sig i.

**Öppna beslut:**

- [ ] **Besluta om programmets namn.** "Löpanalys" (och paketet `lopanalys`, datamappen `%APPDATA%\Lopanalys`) passar inte cykel och SkiErg. Förslag: byt bara det synliga namnet (`ui.app.name`) och låt paketet och datamappen heta som i dag.
- [ ] **Besluta om resten av steg 0 (namnbytet, `Ergometer`-gränssnittet och vyernas enheter) ska vänta på proven på gymdatorn.** Förslag: ja, gör det när bandet, laktattrappan och utskriften är provade. Cykelns enheter och protokoll (steg 1a och 1b) kan börja före, eftersom de läser `load` och profilen.

## 6. Uppskattning för steg 0

| Delsteg | Filer i `src` | Språkfiler | Testfiler | Nya filer |
|---|---|---|---|---|
| 0a | 5 | 0 | 2 | `core/sport.py`, guldtestet (inte lagt än) |
| 0b | 18 | 0 | 14 | – |
| 0c | 11 | 0 | 5 | – |
| 0d | 12 | 4 | 2 | – |
| **Steg 0 totalt (unika filer)** | **cirka 30** | **4** | **cirka 16** | **2** |

Av `src/lopanalys` har 33 filer utanför `pose/` och `camera/` något av namnen `speed_kmh`, `TOPIC_TREADMILL`, `treadmill_mode`, `TestKind` eller `pace`. `camera/` berörs inte alls, och i `pose/` berörs bara `live.py` (ämnets namn). Hela steg 0 är cirka 50 filer av programmets 135.
