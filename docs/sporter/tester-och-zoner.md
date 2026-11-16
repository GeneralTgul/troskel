# Testprotokoll och zoner per sport

*Underlag för fler sporter, skrivet 2026-11-14. Protokollen och analysen är byggda sedan dess (`assessments/library_power.py`, `power.py`, se [Nuläget](ombyggnad.md#nuläget)), med talen i förslagen nedan. Här står vad som är vedertaget för samma tester på cykel (trainer) och SkiErg, förslag på färdiga protokoll i samma form som `assessments/library.py`, hur zonerna skiljer mellan sporter och vad som måste ändras i tröskelmetoderna. Namnbytet `speed_kmh` → `load` står i [ombyggnad.md](ombyggnad.md) och enheterna i [enheter.md](enheter.md).*

**Så här ska uppgifterna läsas:** fem sökningar gjordes genom en sökassistent 2026-11-14, och det som kom därifrån har källan under [Källor](#källor). Allt som är märkt *(ej verifierat mot källa)* är skrivet ur minnet av testfysiologin och är ett förslag, inte ett citat. Olympiatoppens och Svenska Skidförbundets egna testbeskrivningar hittades inte i sökningarna, så det som sägs om dem är genomgående *ej verifierat mot källa*. Zonerna, VO2max och avdragen mellan sporter är kontrollerade i [Kontroll mot källa](#kontroll-mot-källa).

<!-- toc -->
## Innehåll

- [Sammanfattning](#sammanfattning)
- [1. Cykel](#1-cykel)
  - [Laktattrappa](#laktattrappa)
  - [Maxpuls- och ramptest](#maxpuls--och-ramptest)
  - [FTP-test](#ftp-test)
  - [Så uttrycks trösklar och zoner](#så-uttrycks-trösklar-och-zoner)
  - [Förslag på protokoll](#förslag-på-protokoll)
- [2. SkiErg](#2-skierg)
  - [Det som skiljer SkiErgen från band och trainer](#det-som-skiljer-skiergen-från-band-och-trainer)
  - [Laktattrappa](#laktattrappa-1)
  - [Maxtest](#maxtest)
  - [Förslag på protokoll](#förslag-på-protokoll-1)
- [3. Zoner skiljer mellan sporter](#3-zoner-skiljer-mellan-sporter)
- [4. Tröskelmetoderna med watt på x-axeln](#4-tröskelmetoderna-med-watt-på-x-axeln)
- [5. Besluten och frågorna som är kvar](#5-besluten-och-frågorna-som-är-kvar)
- [Kontroll mot källa](#kontroll-mot-källa)
- [Källor](#källor)
<!-- /toc -->

## Sammanfattning

- **Belastningen är watt i båda sporterna.** Tempo per 500 m på SkiErg är bara ett sätt att visa watt.
- **Trainern kan styras (ERG-läge), SkiErgen kan inte.** Åkaren håller själv ett måltempo, och trösklarna ska räknas på den watt som faktiskt hölls under steget.
- **Puls flyttar inte mellan sporter.** Maxpuls och tröskelpuls är lägre på cykel än i löpning, och tröskelpulsen är mycket lägre i stakning. Zoner lagras per idrottare och sport.
- **Tröskelmetoderna räknar rätt med watt på x-axeln.** Det som måste ändras är namn, enheter, tre fasta toleranser i km/h, kurvanpassningens skala och hur zonerna visas.
- **Det finns inget publicerat SkiErg-protokoll att kopiera.** Förslaget följer löpningens trappa och ska provas på riktigt innan det blir standardval.

## 1. Cykel

### Laktattrappa

Enligt sökningen: stegtid 3–8 min, där minst 5 min ofta rekommenderas för att laktatet ska hinna plana ut och 3–4 min är vanligt i praktiken. Stegstorlek 10–50 W, vanligast 25 W (30 W ger ett kortare test men sämre upplösning). Startwatt cirka 100–130 W för en tränad motionär. Provet tas under stegets sista 30 sekunder. En valideringsstudie använde 100 W + 40 W var 8:e minut.

| Del | Vedertaget | Kommentar |
|---|---|---|
| Uppvärmning | 10–15 min lätt, med ett prov före första steget | Samma som löpningen i dag |
| Startwatt | 4 steg under den uppskattade tröskeln | Samma regel som `start_speed_for` |
| Stegstorlek | 20–30 W, eller cirka 0,3–0,4 W/kg *(ej verifierat mot källa)* | Lätta cyklister behöver mindre steg |
| Stegtid | 4–5 min | Kortare steg ger högre tröskelwatt (Jamnick m.fl. 2020, som `library.py` redan hänvisar till) |
| Antal steg | 6–9, till cirka 4–6 mmol/L eller utmattning | |
| Provtagning | Utan att stanna: cyklisten trampar vidare och räcker fram handen eller örat *(ej verifierat mot källa)* | `pause_s = 0` |
| Kadens | Självvald och ungefär lika hela testet, 80–100 varv/min *(ej verifierat mot källa)* | Kadensen påverkar laktatet och loggas per steg |
| Trainer | ERG-läge, så att watten är låst oavsett kadens | |

### Maxpuls- och ramptest

Ett ramptest ökar effekten en gång i minuten till utmattning. Stegstorleken stod inte i sökträffarna. Brukligt *(ej verifierat mot källa)*: start 100–150 W och +20–25 W per minut (eller cirka 6 % av FTP per minut), så att rampen tar 8–12 min efter uppvärmningen. Resultatet är högsta puls och **MAP**, maximal aerob effekt (bästa 1-minuterseffekten).

Maxpulsen på cykel blir lägre än i löpning (avsnitt 3), så ett cykelmax ersätter inte ett löpmax och tvärtom.

### FTP-test

| Test | Så går det till | FTP räknas som | Källa |
|---|---|---|---|
| 20 minuter | 20 min så hårt som möjligt i jämn fart efter uppvärmning. I Coggans original föregås det av 5 min hårt *(ej verifierat mot källa)* | 95 % av medeleffekten | TrainerRoad |
| Ramp | Högre watt varje minut till utmattning | 75 % av bästa 1-minuterseffekten | TrainerRoad |

Ramptestet kan överskatta FTP hos den som har stark spurt. 20-minuterstestet körs utan ERG-läge, eftersom cyklisten själv väljer effekten. Ur ett 30-minuterslopp fås också en tröskelpuls för cykel: medelpulsen under de sista 20 minuterna enligt Friel *(ej verifierat mot källa)*.

FTP och LT2 ur en laktattrappa är inte samma tal. De ligger nära varandra men lagras var för sig med metoden angiven. Enligt sökningen ligger FTP oftast över maximal laktatjämvikt (MLSS): Inglis m.fl. 2020 (18 deltagare) fann MLSS på 88,5 % av 20-minuterseffekten, alltså 93 % av FTP, och Lillo-Beviá m.fl. 2022 (11 cyklister) att en personlig faktor på cirka 91 % stämde bättre än 95 %. En studie från 2019 (15 cyklister) fann ändå hög samvariation (r = 0,91). Alla FTP-protokoll har ett individuellt fel på cirka 3–5 %.

### Så uttrycks trösklar och zoner

- **Watt** vid LT1 och LT2 är huvudvärdet.
- **W/kg** är watt delat med kroppsvikten på testdagen. Vikten sparas därför på testet, inte bara på idrottaren.
- **Puls** vid LT1 och LT2 gäller bara för cykel.
- **Coggans effektzoner** i procent av FTP: Z1 under 55, Z2 55–75, Z3 76–90, Z4 91–105, Z5 106–120, Z6 121–150, Z7 över 150 (gränserna skiljer något mellan källor).
- **Olympiatoppens I1–I5** räknas ur LT1 och LT2 som i dag, med watt i stället för km/h.

### Förslag på protokoll

Samma form som `lactate_step`, `max_hr_ramp` och `lactate_to_max`, med watt som `load` och utan lutning. Nivåerna motsvarar `LEVELS` och anger uppskattad watt vid LT2. Talen är förslag *(ej verifierat mot källa)*.

```python
LEVELS_BIKE = {"beginner": 150, "recreational": 200, "trained": 260, "elite": 330}  # W vid LT2

bike_lactate_step(start_w, step_w=25, stages=7, stage_s=300, pause_s=0,
                  warmup_s=600, warmup_w=None, warmup_lactate=True)
bike_ramp(start_w=100, step_w=20, step_s=60, warmup_s=600, warmup_w=None)   # open_ended
bike_ftp_20(warmup_s=900, effort_s=1200)                                    # inte ERG
```

| Protokoll | Uppvärmning | Steg | Prov | Längd |
|---|---|---|---|---|
| Laktattrappa cykel, motionär | 10 min på 80 W | 100 W, +25 W, 7 steg à 5 min (100–250 W) | Under steget, utan paus | cirka 45 min |
| Laktattrappa cykel, vältränad | 10 min på 120 W | 140 W, +30 W, 7 steg à 5 min (140–320 W) | Under steget, utan paus | cirka 45 min |
| Ramptest cykel (maxpuls, MAP, FTP) | 10 min på 100 W | 100 W, +20 W per minut till utmattning (vältränad: 150 W, +25 W) | Inget, eller ett prov 1–3 min efter | 20–25 min |

*Trappa och max i ett besök* går att göra som `lactate_to_max`: efter sista laktatsteget fortsätter rampen med +20 W per minut.

## 2. SkiErg

### Det som skiljer SkiErgen från band och trainer

- **Ingen styrning.** Motståndet kommer av hur hårt åkaren drar. Programmet visar ett måltempo, läser watt från PM5 och räknar stegets medelwatt. Det är fallet *går inte att styra, men mäter* i [enheter.md](enheter.md#5-förslag-på-klasser-och-gränssnitt). Stegets x-värde ska vara uppmätt medelwatt, inte målet.
- **Watt och tempo** hänger ihop enligt Concept2: `watt = 2,80 / tempo³`, där tempo är sekunder per meter (tiden per 500 m delad med 500). 2:05 per 500 m är 179 W. Sambandet är kubiskt: 5 s snabbare per 500 m är cirka 5 W vid 3:00 men cirka 20 W vid 2:10. Stegen anges därför i watt, och tempot visas för åkaren.
- **Dragfaktorn** (spjället) ändrar känslan och frekvensen men inte watten. Den hålls lika mellan tester och sparas på testet *(ej verifierat mot källa)*.
- **Händerna är upptagna**, så ett prov från fingret kräver paus. Ett prov från örat kan testledaren ta med ett kortare avbrott *(ej verifierat mot källa)*.

### Laktattrappa

Sökningen gav inget fullständigt publicerat protokoll för SkiErg. Det som fanns:

- En studie på australiska landslagsåkare validerade ett stegvis stakprotokoll på Concept2 SkiErg mot skidgång på löpband. Steglängder och wattal stod inte i sammanfattningen.
- En SkiErg-studie från 2025 fann mycket god samvariation med skidgång på löpband för högsta syreupptag (r = 0,95).
- En äldre studie på 11 skidåkare satte överkroppens tröskel där laktatet låg 1,8 mmol/L över värdet efter uppvärmningen.
- Steg på 3 och 5 min förekommer i närliggande protokoll (rodd, överkroppsstakning).
- En andra sökning samma dag gav stegen i en studie från 2015 på Concept2 stakergometer (troligen den australiska ovan, inte kontrollerat): första steget 4 min, sedan steg på 3 min 15 s, vart och ett följt av **45 s stillastående paus för prov från fingret**. Wattstegen stod inte. En norsk guide för submaxtest anger steg på 5 min med 1 min vila och en startnivå där laktatet är högst 1 mmol/L (beskriven för löpband).

**Olympiatoppen och Svenska Skidförbundet** *(ej verifierat mot källa)*: laktatprofilen för längdåkare körs som regel på rullskidor på löpband eller i löpning, med steg på cirka 5 min och en kort paus (30–60 s) för prov, och zonerna I1–I5 sätts ur profilen. Stakergometer används mest för överkroppens kapacitet och för maxtester. Det finns alltså inget förbundsprotokoll för SkiErg att kopiera.

| Del | Förslag | Kommentar |
|---|---|---|
| Uppvärmning | 10 min lätt, prov före första steget | |
| Stegstorlek | 15–20 W | Mindre än på cykel: överkroppen har ett smalare spann mellan lätt och max |
| Stegtid | 4 min | Samma som löpningen. 5 min blir långt när armarna tröttnar lokalt |
| Paus för prov | 30 s (finger), kortare med prov från örat | Dagens `lactate_pause_s` |
| Antal steg | 6–7 | Avbryt när måltempot inte går att hålla |

### Maxtest

Två vägar *(ej verifierat mot källa)*: en ramp med små steg varje minut till utmattning, eller ett tidstest (1000 m eller 3–5 min för fullt). Enligt 2025-studien har 1000 m på SkiErg utmärkt reliabilitet, men svag överensstämmelse med test på snö. Rampen passar programmets nuvarande form bäst (`open_ended`). Högsta pulsen i stakning ligger under löpmaxet även vid full utmattning, så värdet sparas som högsta puls för SkiErg.

### Förslag på protokoll

```python
LEVELS_SKIERG = {"beginner": 110, "recreational": 140, "trained": 190, "elite": 250}  # W vid LT2

skierg_lactate_step(start_w, step_w=15, stages=7, stage_s=240, pause_s=30,
                    warmup_s=600, warmup_w=None, warmup_lactate=True)
skierg_ramp(start_w=80, step_w=15, step_s=60, warmup_s=600)                 # open_ended
```

| Protokoll | Steg | Tempo per 500 m | Prov |
|---|---|---|---|
| Laktattrappa SkiErg, motionär | 60 W, +15 W, 7 steg à 4 min (60–150 W) | 3:00, 2:47, 2:37, 2:29, 2:23, 2:17, 2:13 | 30 s paus efter varje steg |
| Laktattrappa SkiErg, vältränad | 100 W, +20 W, 7 steg à 4 min (100–220 W) | 2:32 till 1:57 | 30 s paus efter varje steg |
| Ramptest SkiErg (högsta puls, toppeffekt) | 80 W, +15 W per minut till utmattning (vältränad: 120 W, +20 W) | från 2:44 | Inget, eller ett prov 1–3 min efter |

Nivåtalen och startwatten är förslag *(ej verifierat mot källa)*.

## 3. Zoner skiljer mellan sporter

Siffrorna gäller samma personer i olika arbetsformer.

| Jämförelse | Löpning | Annan sport | Skillnad | Källa |
|---|---|---|---|---|
| Maxpuls, män | 191 | cykel 187 | 4 slag lägre | Knoepfli-Lenzin, Haenggli & Boutellier, *Optimised heart rate formulae to monitor endurance training in sedentary individuals*, J Sports Sci 2014;32(6):557–562, DOI 10.1080/02640414.2013.843015, 795 stegrande test *(belagt 2026-11-16 ur en sammanfattning av abstractet, inte fulltext)* |
| Maxpuls, kvinnor | 192 | cykel 185 | 7 slag lägre | Samma |
| Puls vid deflektionspunkten (HRDP), män | 179 | cykel 169 | 10 slag lägre | Samma. Punkten är pulsens deflektion, inte laktat: **varken LT1 eller LT2**. Populationen är otränade |
| Puls vid deflektionspunkten (HRDP), kvinnor | 181 | cykel 170 | 11 slag lägre | Samma, se raden ovan |
| Maxpuls, triathleter | | cykel | ofta 6–10 slag lägre | Sökträff, översikt |
| Puls vid laktattröskel, 9 elitskidåkare | 187 (löpning och skate på rullskidor) | stakning 161 | 26 slag lägre (14 %) | *Variations in heart rate at blood lactate threshold due to exercise mode in elite cross-country skiers*, 2006 |
| Högsta syreupptag och maxpuls, längdåkare | | överkroppsarbete | tydligt lägre, exakta tal inte hämtade | Frontiers in Sports and Active Living 2021 |

Kontrollerat mot källa 2026-11-14 ([avsnittet nedan](#kontroll-mot-källa)): 2006-studien är Larson (JSCR 20(4):855–860, A. J. Larson enligt en sökträff, medförfattare inte bekräftade), 9 elitåkare från college (4 män, 5 kvinnor), tröskelpuls cirka 187 i löpning och skate mot cirka 161 i stakning. 2021-studien är Hansen m.fl. (Frontiers in Sports and Active Living). **2013-studien är identifierad 2026-11-16** (läsaren, sökning på DOI:n; tandfonline och pubmed 24073817): Knoepfli-Lenzin, Haenggli & Boutellier (ETH Zürich), 795 stegrande test på otränade kvinnor och män i löpning och cykling. Siffrorna 191/187/179/169 stämmer för männen, och kvinnorna har 192/181 i löpning och 185/170 på cykel. Författarna föreslår 165 − 0,5 × ålder ± 5 slag för löpning och 160 − 0,5 × ålder ± 5 för cykling. Siffrorna kommer ur en sammanfattning av abstractet och ska kontrolleras mot originalet. Punkten är **pulsens deflektion**, inte en laktatgräns, så raderna får inte användas som avdrag för LT1 eller LT2, och populationen är otränad: de säger riktningen (lägre på cykel), inte ett avdrag för en tränad idrottare. Roecker, Striegel & Dickhuth 2003 (Int J Sports Med 24:173–178) fann att pulsen vid individuell anaerob tröskel och vid 4 mmol/L inte ens samvarierade mellan cykel och löpning, och drog slutsatsen att pulsråd måste bygga på test i den egna sporten. En annan sökträff anger 165 slag/min på cykel mot 175 i löpning vid maximal laktatjämvikt.

En direkt jämförelse av **maxpuls** mellan löpning och stakning hittades inte. Sammanfattningen av 2006-studien anger högsta puls 188 i stakning mot 193 i skate, cirka 5 slag lägre (inte kontrollerat mot originalet). Hansen m.fl. 2021 (Frontiers in Sports and Active Living, PMC8724206; 10 män och 10 kvinnor, stakergometer mot löpning) fann att VO2peak och peak-effekt var signifikant lägre i stakning och hänvisar till Sawka 1982 och Undebakke 2019 för att maxpulsen är tydligt lägre vid överkroppsarbete, men siffran för puls kom inte fram i sökningarna 2026-11-15. Brukligt att räkna med är 5–10 slag lägre hos vana stakare och mer hos ovana *(ej verifierat mot källa)*.

Orsaken är mängden arbetande muskler: löpning använder mest, cykel mindre och stakning minst, så laktatet stiger vid lägre puls i de mindre muskelgrupperna *(ej verifierat mot källa)*. Skillnaden är personlig och krymper med träning i sporten, så den kan inte räknas om med ett fast avdrag.

**Slutsats för programmet:**

1. Zoner lagras per **idrottare och sport**, och varje zonuppsättning pekar på testet den kom ur.
2. Maxpuls, tröskelpuls (LT1 och LT2) och tröskelbelastning lagras per sport. Ett löpmax används inte för att räkna zoner i procent av maxpuls för cykel eller SkiErg.
3. Saknas ett test i en sport **räknas zonerna om ur en annan sports test och märks som uppskattade**, med ett avdrag per sport och texten att ett eget test ger säkrare zoner (en uppskattning är bättre än inget). Ett eget test i sporten ersätter alltid uppskattningen.
4. Ålderskriteriet i `max_hr.py` (208 − 0,7 × ålder, inom 10 slag) slår fel oftare på cykel och SkiErg, där toppulsen är lägre. Det behöver ett avdrag per sport eller stängas av där.

En vanlig form för egna zoner är en tröskelpuls per sport (löpning, cykel, SkiErg), zonerna som procent av respektive tröskelpuls, och en egen prestationsreferens per sport (tröskeltempo, FTP, SkiErg 5 km), alla med värde, datum och källa. Maxpulsen brukar då vara gemensam. I Löpanalys bör den vara per sport.

## 4. Tröskelmetoderna med watt på x-axeln

Alla metoder i `lactate.py` arbetar på talpar (belastning, laktat). Ett byte från km/h till watt ändrar inget i matematiken.

| Metod | Oförändrad | Varför, och vad som ska kontrolleras |
|---|---|---|
| Baslinje + 0,5 / 1,0 / 1,5 | Ja | Nivån sätts i mmol/L, x-axeln läses bara av |
| Fast 2,0 och OBLA 4,0 | Ja | Samma skäl |
| Första stegringen (`first_rise`, 0,4 mmol/L) | Ja | Jämför bara laktat mellan steg. Med små steg kan stegringen per steg hamna under 0,4, så villkoret löser ut senare |
| Dmax och mod-Dmax | Ja | Punkten med störst avstånd till linjen är den där kurvan lutar som linjen, och den flyttar sig inte när x-axeln skalas om |
| Log-log | Ja | En omskalning av x är en förskjutning i log-rummet, brytpunkten blir densamma |
| Kurvanpassningen (`exp`, `poly3`) | Ja, med en rättning | I `_fit_exp` är gränserna för `c` (0,0001–5) och startvärdet (lägst 0,01) valda för km/h. I watt blir `c` cirka 0,01–0,03, alltså vid kanten. Dela x med högsta steget före anpassningen |

Dmax, mod-Dmax och log-log ger däremot **andra trösklar om x-axeln är tempo eller fart på SkiErg**, eftersom watt går som farten i kubik. X-axeln ska alltid vara watt.

Det som måste ändras:

1. **Namn och enhet.** `speed_kmh` i `Stage`, `Threshold`, `Zone`, `LactateResult.points` och `MaxHrResult.stage_hr` blir `load` med enheten ur sportens profil ([ombyggnad.md](ombyggnad.md)).
2. **Fasta toleranser i km/h.** `_hr_at` godtar 0,3 från en ensam punkt, valet av LT1 kräver 0,05 under LT2, och dubbletter av samma steg jämförs med 1e-6. De ska vara andelar av belastningen.
3. **Uppmätt belastning.** På SkiErg, och på cykel utan ERG-läge, är stegets x-värde medelwatten som hölls. Två steg kan då hamna nära varandra eller i fel ordning, vilket dagens dubblettregel inte fångar.
4. **Visningen.** `pace_str` ger min/km. Cykel visar W och W/kg, SkiErg visar W och tempo per 500 m (`500 × ³√(2,80 / watt)` sekunder).
5. **Zongränsen I4/I5 utanför testet.** `from_lactate` sätter den till 5 % över LT2 när kurvan inte når dit. Watt har ingen sockel som farten har, så andelen sätts per sport och är sannolikt större i watt *(ej verifierat mot källa)*. Regeln att LT1 inom 5 % av LT2 är "för nära" kan stå kvar.
6. **Jämförbarhet.** Trösklar ur olika stegtider är inte jämförbara. `compare.py` jämför bara tester i samma sport, och varnar när stegtiden skiljer.
7. **Nya värden på testet.** Kroppsvikt på testdagen (W/kg), kadens per steg (cykel), dragfaktor och frekvens per steg (SkiErg), samt FTP som eget resultat med metoden angiven.

`max_hr.py` räknar på pulsen över tid och fungerar oförändrat (toppuls, platå, återhämtning), utom ålderskriteriet i avsnitt 3 och fältnamnet i `stage_hr`.

## 5. Besluten och frågorna som är kvar

**Beslutat 2026-11-14:**

- **Laktattrappan på cykel har steg på 5 min,** i stället för löpningens 4 min.
- **FTP-testerna (20 min och ramp) ingår i första versionen** för cykel.
- **En sport utan eget test får uppskattade zoner** ur en annan sports test, märkta som uppskattning (avsnitt 3). Avdragen per sport är inte bestämda och ska beläggas med källa innan de byggs. **Inte byggt:** [källkontrollen](#kontroll-mot-källa) kunde inte belägga avdragen, så programmet säger i stället i resultatet att sporten saknar eget test.

**Kvar:**

- [ ] **Besluta om SkiErg-trappan ska ha 30 s paus för prov från fingret** som standard. Nej betyder prov från örat med så kort avbrott som möjligt.
- [ ] **Besluta om kroppsvikten ska vara obligatorisk på cykeltester**, så att W/kg alltid kan visas. Byggt så länge: guiden frågar efter vikten när den saknas, och går inte vidare utan den.

## Kontroll mot källa

Kontrollerat 2026-11-14 genom webbsökningar. Olympiatoppens PDF, Garmins support och tidskrifterna gick inte att läsa i fulltext, så det mesta är sammanfattningar av träffar (andrahand). *Stämmer* = träffarna säger samma sak, *Avviker* = träffarna säger något annat, *Ej belagt* = inget hittades.

| Påstående i kod eller docs | Utfall | Källa |
|---|---|---|
| Olympiatoppen I1–I4 i % av maxpuls: 55–72, 72–82, 82–87, 87–92 | **Stämmer** (andrahand) | Sammanfattning av olt-skala.nif.no, 2024-PDF:en |
| I5 92–97 % | **Avviker lite:** träffarna anger 92–100 %. Skalan har dessutom åtta zoner (I-1–I-8), inte fem, och I-6–I-8 saknas i koden | Samma |
| Laktat per zon | **Stämmer (rättat 2026-11-15, olt_2024_no.pdf, hämtad i en webbsökning):** koden har nu Olympiatoppens egna riktvärden per zon: I1 under 1,5 mmol/L, I2 cirka 1,0–2,0, I3 cirka 1,5–3,5, inget värde för I4, och laktatet styr inte I5 (tomt eller "–" i tabellen). Värdena överlappar och är ett intervall per zon, inte gränser mellan zonerna. De är bara för visning och styr inga gränser. Tester som sparades före rättningen visar sina gamla värden | Samma |
| Gränsen I4/I5 vid cirka 6 mmol/L i zonerna ur laktatkurvan (`curve_zones`) | **Programmets egen regel, inte Olympiatoppens.** Olympiatoppen anger inget laktatvärde för I4 och låter inte laktatet styra I5. Gränsen läggs där kurvan når 6 mmol/L (eller LT2 + 2 mmol/L om LT2 är hög), och 5 % över LT2 när kurvan inte når dit | Samma |
| Svenska A1–A3+: 60–75, 75–85, 85–95, 95–100 % | **Stämmer med boken, ej med ett förbund:** Charlotte Kallas övningsbok (Bonnier 2019) har siffrorna, men den översta zonen heter *An*, inte A3+. Inget dokument från Svenska Skidförbundet hittades | bonnierforlagen.se (övningsbok) |
| Friel, löpning i % av LTHR: <85, 85–89, 90–94, 95–99, 100–102, 103–106, >106 | **Stämmer** (andrahand). LTHR är medelpulsen de sista 20 minuterna av ett 30-minuterstest | 220 Triathlon, TrainingPeaks |
| Garmins standardzoner 50–60–70–80–90–100 % av maxpuls | **Stämmer** | Garmins manualer (Forerunner 945, fēnix 6 Pro) |
| Garmins standardprocent för %LTHR | **Ej belagt** (koden använder dem inte) | |
| Polars fem zoner 50–60–70–80–90–100 % av HRmax | **Stämmer** | support.polar.com (sammanfattning) |
| Garmin Connect tar emot pulszoner som slag/min, % av maxpuls, % av pulsreserv eller % av LTHR | **Stämmer.** Byt bas under *Zoner > Baserat på*. Vägen i appen: Enheter > Inställningar > Användarprofil > Puls- och effektzoner > Puls (tredjepartsguide, inte Garmins sida) | Garmins manualer, gadgetsandwearables.com |
| Antal gränser i Garmin Connect, och hur zon 1 och 5 ställs | **Ej belagt** (sökt igen 2026-11-16: Garmins manualer för fēnix 3 och 5 säger bara att zonerna redigeras per zon i de fyra baserna och kan sättas per sportprofil; inget om antal gränser eller zon 1:s nedre och zon 5:s övre gräns) | Garmins manualer (www8.garmin.com/manuals/webhelp/fenix5) |
| ACSM:s löpekvation 3,5 + 0,2·v + 0,9·v·lutning (v i m/min) | **Stämmer.** Gäller stabilt tillstånd och över 134 m/min. Felet saknas som publicerat SEE, men oberoende studier visar överskattning: +9,8 ml/kg/min vid maxtest hos 99 aktiva, +14,6 % hos idrottare i Bruce-protokoll, 88 % av 33 löpare överskattades | pubmed 24376318, PMC10747607 m.fl. |
| Léger–Mercier 1984: VO2 = 3,5 × km/h | **Avviker:** träffarna anger bruttokostnaden 2,209 + 3,163 × km/h på löpband (sammanvägning av 10 studier, 130 personer), alltså cirka 4–6 % under 3,5 × v vid 12–18 km/h. Ekvationen ligger nära ACSM:s (3,5 + 3,33 × v), så de två är **inte oberoende belägg** | bisp-surf.de, scilit.net (sammanfattningar) |
| Felmarginalerna 10 % och 15 % | **Ej belagt som statistik.** De är arbetsvärden. Underlag: ACSM-ekvationen överskattar, Uth–Sørensen har SEE 4,7 ml/kg/min (7,8 %) med åldersberäknad maxpuls | |
| Uth–Sørensen 2004: 15,3 × HRmax / HRrest | **Stämmer.** Faktorn 15,3 (SD 0,7) kommer från en delgrupp på 10 män. Totalt antal försökspersoner kom inte fram. Gäller vältränade män 21–51 år. Senare validering (Eur J Appl Physiol 2022): ingen skillnad hos tränade män, cirka +2 ml/kg/min hos otränade. Programmets 15 % är ett grovt arbetsvärde, och metoden är sämre för kvinnor och otränade | link.springer.com 10.1007/s00421-003-0988-y, 10.1007/s00421-022-04928-0 |
| Extrapolering mot maxpuls (steg ur ACSM-ekvationen) | **Ej belagt** som egen källa. SEE cirka 4–5 ml/kg/min nämns för submaximala protokoll | |
| Maxpuls cykel lägre än löpning | **Riktningen stämmer** (Millet, Vleck & Bentley 2009, Sports Med 39:179–206), men storleken i slag för maxpulsen kom inte fram (sökt igen 2026-11-16; artikeln DOI 10.2165/00007256-200939030-00002 måste läsas i fulltext) | westminsterresearch.westminster.ac.uk |
| Roecker, Striegel & Dickhuth 2003: 371 personer (304 män, 67 kvinnor), pulsen vid individuell tröskel hänger inte ihop mellan cykel och löpning | **Stämmer** (Int J Sports Med 24:173–178) | thieme-connect.de 10.1055/s-2003-39087 |
| Uppvärmning på löpband: minst 6 min innan stegen stabiliserats (Lavcanska, Taylor & Schache 2005, Hum Mov Sci) | **Stämmer i andrahand** (6 min; inte 6–8). Individuellt, går inte att generalisera | typeset.io, scilit.com |
| 1 % lutning motsvarar utomhuslöpning (Jones & Doust 1996, J Sports Sci 14:321–327) | **Stämmer** för 9 vältränade män, upp till cirka 15 km/h. Vid 18 km/h låg motsvarigheten mellan 1 och 2 % | pubmed 8887211 |
| Armbågsvinkel 60–120° från sidan, och armpendel | **Ej belagt.** Tränarråd säger 70–110° eller cirka 90°. Studier (Kram/Arellano, Yegian & Lieberman 2019) finner liten eller ingen effekt på löpekonomin. Inget riktvärde för armpendeln hittades | PMC11656458, phys.org |
| Friels avdrag för cykel-LTHR | **Belagt (Friel själv, andrahand):** löp-LTHR ligger nästan alltid högre än cykel-LTHR, vanligen 5–10 slag. Tumregel: dra 7 slag från löp-LTHR (eller lägg 7 på cykel-LTHR) och bekräfta med fälttest. Hans cykelzon 1 är under 81 % av LTHR mot 85 % i löpning. Att maxpulsen är lägre på cykel står inte i utdragen. Inomhustest kan ge ett onormalt lågt cykelvärde | trainingbible.com/joesblog/2009/01/tests-and-measurements.html (Joe Friel, januari 2009), trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones/ (läsaren 2026-11-15, sammanfattning av träffar) |
| Stakning: maxpuls 5–10 slag lägre | **Ej belagt.** Hansen m.fl. 2021 säger att toppulsen är tydligt lägre vid överkroppsarbete, utan siffra. Tröskelpulsen 161 mot 187 stämmer med Larson 2006 | Frontiers 10.3389/fspor.2021.762794 |

## Källor

Hämtade genom webbsökningar 2026-11-14. Det är sammanfattningar av sökträffar, sidorna är inte lästa i sin helhet.

- **Cykel, laktatprotokoll:** roadmancycling.com (guide till laktattest), inscyd.com (lactate testing cycling), pmc.ncbi.nlm.nih.gov/articles/PMC7672951 (valideringsstudie, 100 W + 40 W per 8 min), highnorthperformance (ramptest).
- **SkiErg:** pmc.ncbi.nlm.nih.gov/articles/PMC4699693 (stegvis protokoll på Concept2 SkiErg, australiska landslagsåkare), frontiersin.org 10.3389/fspor.2025.1631229 (SkiErg mot löpband och snö), lida.sport-iat.de (överkroppens anaeroba tröskel), concept2.com.
- **Puls mellan sporter:** informahealthcare.com 10.1080/02640414.2013.843015 (795 stegrande test, löpning mot cykel), sponet.de Record 4014142 (puls vid laktattröskel hos elitskidåkare, 2006), frontiersin.org 10.3389/fspor.2021.762794 (över- och underkroppsarbete hos längdåkare).
- **FTP, zoner och Concept2-formeln:** trainerroad.com (FTP-test och effektzoner), concept2.com/training/watts-calculator.
- **Ur en andra omgång om fem sökningar samma dag** (två sessioner arbetade med samma uppdrag): Inglis m.fl. 2020 och Lillo-Beviá m.fl. 2022 (FTP mot MLSS), pubmed.ncbi.nlm.nih.gov/30676826 och 31714462, Roecker m.fl. 2003 (thieme-connect.de 10.1055/s-2003-39087), Millet, Vleck & Bentley 2009 (Sports Med 39:179–206, DOI 10.2165/00007256-200939030-00002; abstractet säger bara att pulsen skiljer sig vid max och submax, siffran för maxpuls kom inte fram i en andra sökning 2026-11-15; Roecker 2003 ger 165 ± 8 mot 175 ± 10 slag/min vid utmattning, cirka 10 slag lägre på cykel hos icke-triatleter), AusCycling *Physiology endurance testing guidelines* 2026 (steg 10–50 W efter kategori), frontiersin.org 10.3389/fphys.2021.749356, myranorge.substack.com/p/submax-test.
- **Redan i koden:** Jamnick m.fl. 2020 (Sports Med 50:1729), Bishop m.fl. 1998, Cheng m.fl. 1992, Beaver m.fl. 1985, Tanaka m.fl. 2001.

**Inte hittat, värt att hämta före byggandet:** Olympiatoppens testprotokoll för laktatprofil, och metodavsnittet i den australiska SkiErg-studien (gick inte att läsa i fulltext).
