# Träningsplanen

*Hur programmet bygger ett träningsschema på testerna: datamodellen, planmotorn, säkerheten och utskriften. Planmotorn, utskriften, kalenderfilen, lagringen och sidan *Träningsschema* i programmet är byggda (se [Nuläget](#nuläget-vad-som-är-byggt)). Motorn fyller flera idrotter med varje idrotts egna pass, styrka och simning med ett manuellt simtest; formuläret och utskriften har idrotterna och antalet styrkepass, men simtestet går inte att göra i programmet än. Målet är ett komplett test- och träningsprogram där idrottaren vet vad varje pass ska innehålla, och där träningen är skräddarsydd efter idrottarens kapacitet och mål.*

Allt som är märkt *(ej verifierat)* är skrivet ur minnet.

<!-- toc -->
## Innehåll

- [Sammanfattning](#sammanfattning)
- [Nuläget: vad som är byggt](#nuläget-vad-som-är-byggt)
- [1. Datamodellen](#1-datamodellen)
- [2. Planmotorn](#2-planmotorn)
  - [Blocken](#blocken)
  - [Nivåerna](#nivåerna)
  - [Intensitetsfördelningen](#intensitetsfördelningen)
  - [Flera idrotter](#flera-idrotter)
  - [Delmål och planer utan mål](#delmål-och-planer-utan-mål)
  - [Återhämtning efter ett lopp](#återhämtning-efter-ett-lopp)
  - [Omtest och omläggning](#omtest-och-omläggning)
  - [Det rullande schemat](#det-rullande-schemat)
  - [Passbiblioteket](#passbiblioteket)
  - [Var en språkmodell kan hjälpa senare](#var-en-språkmodell-kan-hjälpa-senare)
- [3. Säkerhet och ansvar](#3-säkerhet-och-ansvar)
- [4. Utskriften](#4-utskriften)
<!-- /toc -->

## Sammanfattning

- **Planmotorn är en ren Python-modul** (`plan/`, ingen Qt) som Windows-programmet använder och som en webbtjänst senare kan använda. Den är regelbaserad: varje pass kommer ur ett passbibliotek, och varje val går att förklara.
- **Planen lagrar zoner, inte siffror.** Målpuls, målfart och målwatt räknas fram ur det senaste testet när planen visas eller skrivs ut. Ett omtest flyttar då alla mål utan att planen skrivs om.

## Nuläget: vad som är byggt

Motorn finns i `src/lopanalys/plan/`, utskriften i `src/lopanalys/report/plan_builder.py` och sidan *Träningsschema* i `src/lopanalys/ui/views/plan.py`. Coachen gör schemat i programmet, se [handboken](handbok/traningsschema/traningsschema.md). Exempelkommandot gör ett schema utan programmet:

```bash
source scripts/headless_env.sh        # Linux utan skärm
python -m lopanalys.plan.demo --weeks 12 --out exempel.pdf --png forstasida.png
python -m lopanalys.plan.demo --weeks 26 --no-goal --level beginner --lang en --out halvar.pdf
```

Kommandot gör ett schema för demolöparen (aldrig en riktig person): PDF, kalenderfil bredvid PDF:en och, med `--png`, förstasidan som bild. Med mål slutar schemat med ett halvmaraton och har ett seedningslopp fem veckor före. Med `--no-goal` slutar det med ett test.

| Del | Läge |
|---|---|
| Indata: idrottare med kapacitet per sport, idrotter med roll och andel, tillgänglighet per veckodag, mål (A, B, C) eller längd, nivå och nuvarande veckovolym | Byggt, också i formuläret ([handboken](handbok/traningsschema/traningsschema.md#flera-idrotter)) |
| Block bakåt från målet, lätta veckor, omtest, volymökning, långpass, kvalitetspass med vila emellan | Byggt, se [Planmotorn](#2-planmotorn) |
| Passbibliotek för löpning, cykel, SkiErg, simning och styrka med texter på svenska och engelska | Byggt. Skidor har SkiErgens pass, se [Passbiblioteket](#passbiblioteket) |
| Zon till puls och fart ur testet (löpning), watt (cykel), watt och tid per 500 m (SkiErg), uppskattade zoner märkta, schema utan test | Byggt |
| Ett omtest flyttar målen i puls och fart från en dag och framåt, utan att något pass byter dag | Byggt (`resolve.retest`), och erbjuds i programmet efter ett nytt test, se [Omtest och omläggning](#omtest-och-omläggning) |
| Utskrift: översikt, läsanvisning och ett blad per vecka | Byggt. Utskriften på Windows är inte provad ([PROV.md](PROV.md)) |
| Kalenderfil (`.ics`) | Byggt. Importen i en telefon är inte provad |
| Rullande schema: pass fyra veckor fram, skiss för resten, *Förnya* utan att fylla i något | Byggt, se [Det rullande schemat](#det-rullande-schemat) |
| Lagring i databasen: ett schema per idrottare, med det som angavs, testet, schemat som det byggdes och en logg | Byggt (migrering 9, se [Datamodellen](#1-datamodellen)) |
| Sidan *Träningsschema*: formulär, förhandsvisning, spara, skriv ut, PDF, kalenderfil och e-post till idrottaren | Byggt |
| Hälsodeklarationen som tre rutor som krävs före utskrift, PDF, kalenderfil och e-post | Byggt i enkel form, se [Säkerhet och ansvar](#3-säkerhet-och-ansvar) |
| Ombyggnad av resten av planen (nya pass och ny volym) efter ett omtest, sjukdom och missade pass | Inte byggt (resten av steg P4) |
| Flera idrotter: veckotiden fördelad efter andelar, hårda dagar och långpass över idrotterna, platser med flera idrotter | Byggt (steg 1 av 4), se [Flera idrotter](#flera-idrotter) |
| Cykelns och SkiErgens egna pass med mål i watt och tid per 500 m, sweet spot som 88–94 % av tröskelwatten och ett eget test per idrott | Byggt (steg 2 av 4), se [Passbiblioteket](#passbiblioteket) och [Testet per idrott](#testet-per-idrott) |
| Styrka (0–3 pass i veckan ovanpå uthållighetsveckan) och simning på ansträngning eller på ett manuellt CSS-test | Byggt i motorn och i appen (steg 3 av 4), se [Styrka](#styrka) och [Simning](#simning). Antalet styrkepass väljs i formuläret, och simtestet skrivs in från *Historik* |
| Idrotterna i formuläret och utskriften: roll och andel, idrotterna per veckodag, en färg per idrott, idrottstabellen, watt i zonerna, veckans tid per idrott och belastningen | Byggt (steg 4 av 4), se [Utskriften](#4-utskriften). Inte provat på Windows ([PROV.md](PROV.md)) |
| Två pass samma dag: en andra tid på en veckodag ger ett lugnt pass i en annan idrott | Byggt, se [Två pass samma dag](#två-pass-samma-dag) |
| Ett mindre långpass i en stödjande idrott varannan vecka | Byggt, se [Flera idrotter](#flera-idrotter) |
| Återhämtning efter ett lopp: lugna dagar efter ett A-lopp som inte är schemats mål, efter ett långt B-lopp och efter ett lopp strax före schemat | Byggt, se [Återhämtning efter ett lopp](#återhämtning-efter-ett-lopp) |
| Passkort | Inte byggt |

## 1. Datamodellen

Dataklasser utan ORM, som resten av programmet (`core/models.py`). De ligger i `plan/models.py`. Sporten är värdet ur `core/sport.py` (`RUNNING = Sport.RUN.value`, alltså `"run"`). Scheman som sparades med den gamla nyckeln `"running"` läses som `"run"` (`plan/codec.py`).

```
PlanRequest                      det idrottaren och coachen anger
  athlete_name, start: date      schemat börjar första måndagen från och med start
  availability: [Slot]
  capacity: [Capacity]           en per sport
  sports: [str]                  idrotterna när roles är tomt; den första (eller A-målets) är huvudidrott
  roles: [SportRole]             idrotterna med roll och andel; tomt i scheman sparade före flera idrotter
  strength_sessions: int         styrkepass i veckan, 0–3 (coachen anger); sparas bara när det är satt
  hard_days: [HardDay]           fasta hårda dagar (coachen anger); sparas bara när de finns
  goals: [Goal]                  tomt = plan utan tävlingsmål
  duration_weeks: int | None     längden när inget A-mål finns (förval 12)
  level: "beginner" | "recreational" | "trained" | "high"
  recent_weekly_minutes          snitt de senaste 4–6 veckorna, om känt

Slot                             ett tillfälle i veckan
  weekday, max_minutes, start: time | None
  sports: [str]                  vad som går att träna då
  role: "core" | "extra"         bara kärnplatser fylls

HardDay                          en fast hård dag, till exempel ett gruppass
  weekday: int                   0 = måndag
  sport: str                     kvalitetspassets idrott; "" = motorn väljer

SportRole                        en idrott i planen
  sport: str                     "run", "bike", "skierg", "ski" eller "swim" (models.PLAN_SPORTS)
  role: "main" | "support" | "maintenance"     huvudidrott, stödjande eller underhåll
  share: int                     andel av veckotiden i procent; 0 = rollens förval 60, 30 eller 10

Goal
  name, date, sport, distance_km
  priority: "A" | "B" | "C"      A = målet, B = delmål (seedningslopp), C = träningslopp
  target: str                    fri text, t.ex. "under 1:45"

Capacity                         nuvarande nivå i en sport
  anchors: Anchors | None        maxpuls, LT1 och LT2 som puls och fart (assessments/zone_systems.py)
  tested: date, source: str      testets datum och namn
  speed_line: SpeedLine | None   zontestets samband puls -> fart (lutning, skärning, uppmätt fartspann)
  css_s100: float | None         simning: CSS-testets tempo i sekunder per 100 m (assessments/css.py)
  estimated                      sant utan uppmätta trösklar

Plan
  id, request, created, engine_version, zone_system ("garmin")
  based_on: {sport: datum}       testerna planen bygger på
  blocks: [Block], weeks: [Week], notes: [Note]
  status: "draft" | "active" | "replaced"
  horizon: int | None            sista veckan med pass; None = alla veckor har pass (scheman före det rullande schemat)

Block
  kind: "base" | "build" | "peak" | "taper"
  first_week, weeks, start, reason: Note      varför blocket ligger här

Week
  index, monday, block
  kind: "load" | "easy" | "test" | "taper" | "race"
  target_minutes                 taket veckan fylldes under
  workouts: [Workout], notes: [Note]
  sketch: WeekSketch | None      None = veckan har pass; annars bara summorna (tid, pass, hårda dagar,
                                 längsta passet, tid per zon) och veckans lopp

Workout
  key: str                       fast nyckel "<plan>-<åå><vv>-<dag>", överlever ändringar och export
  date, start, sport, template_id, family
  quality: bool                  hård dag: kvalitetspass, test eller lopp
  parts: [Part], goal: Goal | None
  status: "planned" | "done" | "moved" | "missed" | "skipped"

Part                             en passdel
  kind: "warmup" | "steady" | "work" | "cooldown" | "test" | "race" | "technique" | "strength" | "mobility"
  duration_s, repeat, rest_s, rest_zone
  zone: str | None               zonkod i Garmins fem zoner, t.ex. "Z4"
  band: str                      ett smalare mål inom zonen: "sweetspot" på cykeln, annars ""; sparas bara när det är satt
  rpe: int                       ansträngning 1–10, visas alltid

Note                             en text som nyckel och värden, så att planen går att skriva ut på båda språken
```

`Plan.capacity_log` håller de tidigare testerna efter ett omtest: en dag och kapaciteten som gällde före den. `Plan.capacity_on(sport, dag)` ger kapaciteten ett pass visas med.

`PlanRequest.plan_roles()` ger idrotterna i ordning med huvudidrotten först, en gång var och med exakt en huvudidrott: den första som angetts som huvudidrott, annars A-målets idrott, annars den första. `Workout.load` och `Week.load` är belastningen: minuter × zonfaktor (Z1 0,5, Z2 1, Z3 1,5, Z4 2,5, Z5 3,5; ett test räknas som Z4) × idrottsfaktor (löpning, SkiErg och skidor 1,0, cykel och simning 0,7, styrka 0,8).

**Inte i modellen än:** utrustning per plats och gruppträningar med fast innehåll. En fast hård dag (`HardDay`) säger bara att dagen är hård och i vilken idrott, inte vad gruppen gör: passet kommer ur motorns mallar.

**Tre regler i modellen:**

1. **Passdelen har en zon, inte en puls.** Målpuls och målfart räknas ur `Capacity` när passet visas, skrivs ut eller exporteras (`plan/resolve.py`). Fart anges bara där testet mätte den, alltså vid trösklarna och mellan dem. Saknar idrottaren ett test visas bara zonens namn och ansträngningen, och med bara maxpuls märks zonerna som uppskattade. Efter testet *Puls- och fartzoner* är zonerna uppskattade ur maxpulsen från trappan, och varje gräns får en fart ur sambandet mellan puls och fart i stegen (`hr_zones.zone_speeds`, samma som resultatsidan): lite utanför stegen märkt *≈*, längre ut ingen fart. Ett zontest som stoppades innan trappan nådde en minut ger ingen maxpuls (`services.plan_capacity`).
2. **Planen pekar på testerna den bygger på** (`based_on`). `resolve.retest()` ger samma plan på ett nytt test: zonerna ligger kvar och målen följer de nya trösklarna, från en angiven dag eller i hela planen.
3. **Varje block och varje ändring har ett skäl i klartext** (`reason`). Det är det som gör planen förklarlig.

**Lagring:** migrering 9 i `storage/db.py`, efter huvudgrenens version 8 (sporten), lägger till två tabeller och ändrar inget annat. `tests/storage/test_plan_migration.py` bygger en databas i version 8 med e-post, klockans VO2max, ett cykeltest och maxpuls per sport, och visar att allt finns kvar efter migreringen och att kopian är kvar i version 8.

| Tabell | Innehåll |
|---|---|
| `plans` | En rad per idrottare (`athlete_id` är unik): `session_id` (testet schemat bygger på), `request_json` (tillgänglighet, mål, delmål, längd, nivå, veckovolym och testets trösklar), `plan_json` (schemat som det byggdes: block, veckor, pass och loggen över tidigare tester), `health_json` (hälsodeklarationens rutor och datum), `engine_version`, `created`, `updated` |
| `plan_events` | Loggen: tid, händelse (`saved`, `changed`, `retest`, `renewed`, `phone`) och detaljer, till exempel vilka två tester ett omtest gick mellan |

- **Schemat sparas som det byggdes** (`plan/codec.py` gör om det till JSON och tillbaka). Det byggs inte om när det läses, så ett sparat schema ändras inte av en ny programversion. `engine_version` säger vilka regler som byggde det, eller som senast förnyade det. Formatet är 2: `horizon` och skissveckorna (`sketch`) sparas med schemat. Ett schema i format 1 saknar dem och läses som ett schema där alla veckor har pass. Idrotternas roller sparas under `roles` i det som angavs. Ett schema som sparades före dem saknar fältet och läses utan roller, alltså med idrotterna i `sports`. En passdels band (`band`) sparas bara på delar som har ett, så övriga pass sparas som förut och äldre scheman läses utan band. De fasta hårda dagarna sparas under `hard_days` (`{"weekday": 3, "sport": "ski"}`, utan `sport` när motorn väljer) bara när de finns.
- **Namnet sparas inte i schemat.** Det läses från idrottarens rad när schemat öppnas.
- **Målen följer testet.** När schemat öppnas räknas trösklarna om ur testet det bygger på, så att ett rättat laktatvärde slår igenom. Är testet borttaget gäller trösklarna som sparades med schemat.
- **Radering:** raderna följer idrottaren (`ON DELETE CASCADE`) och skrivs över som övriga uppgifter. Tas testet bort ligger schemat kvar utan test. Schemat är med i utdraget (*Exportera allt*). PDF:en och kalenderfilen registreras som sparade filer, så att raderingen hittar dem.

## 2. Planmotorn

Modulen `src/lopanalys/plan/`:

| Fil | Innehåll |
|---|---|
| `models.py` | Dataklasserna i [datamodellen](#1-datamodellen) |
| `library.py` | Passbiblioteket som data: löpningens pass, varje annan idrotts pass på löpmallarnas platser, och styrkans pass utan zoner |
| `periodize.py` | Block, veckotyper och veckovolym: var den börjar, vad veckan rymmer och hur fort den växer |
| `engine.py` | `build_plan()`: passen läggs i platserna |
| `resolve.py` | Zon till puls och fart (fart bara för löpning), och `retest()` |
| `text.py` | Passen som text på aktuellt språk, för utskrift och kalender, och översiktens rad om var tiden börjar |
| `rolling.py` | Det rullande schemat: `roll_forward` ger nästa veckor pass ur den sparade skissen |
| `expected.py` | Vad schemat räknar med mellan två tester och hur mycket av det som blev gjort, för rapportens *Mot schemat* |
| `ics.py` | Kalenderfilen |
| `codec.py` | Schemat och det som angavs som JSON och tillbaka, för lagringen |
| `demo.py` | Exempelkommandot |

Ingen Qt, inga nätanrop och ingen slump: samma indata ger samma plan. Det som återstår är `adapt.py` (ombyggnad av resten av planen efter omtest, sjukdom och missade pass).

### Blocken

Motorn räknar bakåt från A-målet. Finns flera A-mål slutar planen med det sista, och de tidigare behandlas som delmål.

| Block | Längd | Innehåll |
|---|---|---|
| **Nedtrappning** | 1 vecka, 2 veckor för lopp på 25 km eller mer | Volymen ned till ungefär hälften, farten kvar i korta inslag. Loppveckan har ett kort fartpass minst tre dagar före start |
| **Topp** | 2 veckor, 1 vecka i en plan under 10 veckor | Volymen står still. Mål på 20 km eller mer får långpass med de sista 20 minuterna i zon 3 |
| **Uppbyggnad** | 4–8 veckor | Tröskelintervaller och hårda intervaller som blir längre steg för steg |
| **Grund** | Resten av tiden | Volym i de låga zonerna och ett kvalitetspass i veckan: fartlek, backe och tröskel i tur och ordning |

- **Lätta veckor:** tre belastningsveckor och en lätt vecka (65 % av volymen). Nybörjare får två och en. Veckorna räknas bakåt från nedtrappningen eller testet, så att veckorna närmast målet är belastningsveckor. Planen börjar aldrig med en lätt vecka.
- **Volymen** börjar på den kända veckovolymen (*Tränar nu*). Är den okänd börjar den på nivåns förval (se [Nivåerna](#nivåerna)), men högst på 70 % av det veckan rymmer, så att schemat aldrig börjar vid taket. **Det veckan rymmer** är nivåns antal pass i de längsta kärnplatserna, med långpasset inom schemats längsta långpass och de andra passen inom nivåns längsta lugna pass (`periodize.week_capacity`).
- **Ökningen** sprids över grund och uppbyggnad: varje belastningsvecka växer med den takt som når hela veckan vid den sista belastningsveckan före toppen, men aldrig fortare än nivåns takt (5, 7 eller 8 %). Ett schema som börjar nära taket växer alltså långsamt, och ett som börjar långt under växer i nivåns takt så många veckor som behövs, även om taket aldrig nås. En vecka är aldrig mer än 10 % större än den största veckan före, och aldrig större än kärnplatserna rymmer. Startpunkten och var tiden växer till står i översikten (`plan.text.start_line`).
- **Översikten visar progressionen:** under staplarna med tid per zon ritas det längsta passet och minuterna i zon 3–5 per vecka, i en egen panel med egen axel i minuter (ingen andra axel på staplarna). Där syns långpassets ökning och kvalitetspassens steg även när veckotiden står still. Tioprocentsregeln är en tumregel med svagt stöd i forskningen *(ej verifierat)*, så den används som ett tak och inte som ett mål.
- **Långpasset** ligger i veckans längsta plats, är ungefär en tredjedel av veckan och växer högst 15 minuter från en belastningsvecka till nästa.
- **Långpassets tak följer målet** (`periodize.long_cap`, motor 9): nivåns tak gäller, men ett mål som tar mer än fyra timmar höjer det med tre fjärdedelar av varje minut över de fyra timmarna, högst till 4:30. Loppets tid räknas ur distansen och en vanlig tävlingsfart för en tränad motionär i idrotten: 11 km/h i löpning, 15 km/h på skidor och SkiErg, 28 km/h på cykel och 3,5 km/h i simning (`periodize.RACE_KMH`, programmets egna tal). Vasaloppet (90 km på skidor, ungefär 6 timmar) ger *Vältränad* och *Hög volym* 4:30 och *Motionär* 4:00. Ett lopp på 10 km, ett maraton och ett skidlopp på 45 km behåller nivåns tak. Taket gäller också den stödjande idrottens mindre långpass.
- **För kort tid till målet:** blocken kortas i ordningen grund, uppbyggnad, topp. Är det mindre än 6 veckor kvar säger planen det i klartext och bygger ingen topp.
- **Återhämtningen efter ett lopp** står i [Återhämtning efter ett lopp](#återhämtning-efter-ett-lopp).
- **Inte byggt:** två och en för idrottare över 50 år.

### Nivåerna

Coachen väljer nivån (`PlanRequest.level`, `periodize.LEVEL`). Den sätter förvalet för veckotiden när *Tränar nu* är okänd, takten veckorna växer i, hur många belastningsveckor som kommer före en lätt vecka, hur många pass veckan rymmer, var kvalitetspassens stegring börjar, och hur långa långpasset och de lugna passen får bli.

| Nivå | Förval | Ökning | Belastningsveckor | Pass i veckan | Långpass | Lugnt pass | Återhämtning |
|---|---|---|---|---|---|---|---|
| *Nybörjare* (`beginner`) | 2 h | 5 % | 2 | 3 | 1:30 | 75 min | 45 min |
| *Motionär* (`recreational`) | 3 h | 7 % | 3 | 5 | 2:30 | 75 min | 45 min |
| *Vältränad* (`trained`) | 5 h | 8 % | 3 | 7 | 3:00 | 75 min | 45 min |
| *Hög volym* (`high`, motor 9) | 7 h | 8 % | 3 | 11 | 3:00 | 1:45 | 1:00 |

- **Hög volym** är för den som tränar 10–13 timmar i veckan, till exempel inför ett långlopp: fler pass i veckan, också två pass samma dag (se [Två pass samma dag](#två-pass-samma-dag)), och lugna pass upp till 1:45. Återhämtningspasset dagen efter ett hårt pass eller långpasset får bli en timme. Långpassets tak höjs för långa mål som för de andra nivåerna. Med platser som rymmer det (sju dagar med 1:45, en lång lördag och tre luncher för ett andra pass) kan veckorna bli 11–14 timmar.
- **Lugna pass och återhämtning** har nivåns längd (`Level.easy_max`, `Level.recovery_max`). Mallen *Lugn löpning* har 75 minuter och *Återhämtningsjogg* 45 minuter för de tre första nivåerna, som förut.

### Intensitetsfördelningen

**Pyramidal fördelning i grundblocket och mer polariserad i uppbyggnad och topp.** Mätt i tid ligger cirka 80 % under LT1, alltså i zon 1 och 2 i Garmins fem zoner, som är programmets förval. I exempelschemat är det 87 %.

- **Varför inte strikt 80/20 för alla:** fördelningen kommer ur studier av elitidrottare med stor volym (Seiler). En motionär med 4–6 timmar i veckan får för lite belastning om allt över LT1 stryks, och tröskelnära träning ger mycket på få timmar *(ej verifierat mot källa)*.
- **Regeln räknas i pass, inte bara i tid:** högst två kvalitetspass i veckan, ett vid färre än fyra pass och i grundblocket, aldrig två dagar i rad (inte heller över veckogränsen), och aldrig dagen före ett långpass.
- **Högst en fjärdedel av veckans tid över LT1.** Blir det mer stegar motorn ned kvalitetspasset eller stryker det. Den stegar aldrig upp för att fylla en vecka.
- **Fasta hårda dagar** (`PlanRequest.hard_days`, motor 9): coachen anger veckodagar med ett hårt pass som alltid ligger fast, till exempel gruppens kvalitetspass på torsdagen, och för varje dag en idrott eller *valfri idrott*. Dagen är alltid en av veckans dagar med pass, och veckans kvalitetspass läggs där först: av de lagliga valen (aldrig två hårda dagar i rad, aldrig dagen före långpasset, inte runt ett lopp eller i återhämtningen efter ett) vinner det med flest fasta dagar, sedan det med den först angivna dagen, sedan det med mest vila emellan. En vecka med ett kvalitetspass tar alltså den första dagen, och en vecka med två tar båda när de har en dag emellan. En fast dag som inte är laglig den veckan blir en lugn dag. Med en idrott blir passet i den idrotten när dagens plats tillåter den, också en underhållsidrott. Testet ligger kvar i huvudidrotten. Dagarna runt planeras som förut: dagen efter en hård dag blir ett återhämtningspass. Utan fasta dagar blir schemat detsamma som förut.
- **Zonen mellan LT1 och LT2** används medvetet (tröskelintervaller), inte av misstag. Lugna pass har ett tak strax under LT1, och det taket är det viktigaste testet ger en motionär.

### Flera idrotter

Idrottaren anger idrotterna med en roll och en andel av veckotiden (`PlanRequest.roles`, beslutet i #666 2026-11-15). Utan roller gäller idrotterna i `sports`, och ett schema med en idrott blir detsamma som förut. Coachen väljer idrotterna, rollerna och andelarna i formuläret ([handboken](handbok/traningsschema/traningsschema.md#flera-idrotter)). Ett schema med bara löpning sparas utan roller, som förut.

- **Rollerna:** *huvudidrott*, *stödjande* och *underhåll*, med andelen 60, 30 och 10 % som förval. Andelarna är vikter: 60 och 30 ger två tredjedelar och en tredjedel.
- **Platserna:** en kärnplats används när den tillåter någon av planens idrotter. En plats för en idrott som planen inte har lämnas tom. Långpassets plats är huvudidrottens längsta plats.
- **Veckan fylls som förut, över alla idrotter på en gång.** Antalet pass, de hårda dagarna, långpasset, taket på 10 % i veckan och taket på en fjärdedel av tiden över LT1 räknas alltså över idrotterna: en hård dag är en hård dag oavsett idrott, aldrig två i rad (inte heller över veckogränsen) och aldrig dagen före långpasset.
- **Sedan får varje pass sin idrott** (`engine._Filler._sports`): långpasset och testet i huvudidrotten, de hårda passen i datumordning och de lugna med det längsta först, var och ett till den idrott som ligger längst efter sin andel av veckans minuter bland de idrotter platsen tillåter. Ett lopp är i målets idrott.
- **Närmare målet** tar huvudidrotten hälften av de andra idrotternas andel i toppen och tre fjärdedelar i nedtrappningen (`engine.SHARE_SHIFT`).
- **Underhållsidrotten** får högst ett pass i veckan och aldrig ett hårt.
- **Ett mindre långpass i den stödjande idrotten** (`engine.SUPPORT_LONG_MIN`, `SUPPORT_LONG_OF_MAIN`): varannan belastningsvecka i grund och uppbyggnad, utan lopp i veckan, får den första stödjande idrotten ett lugnt långpass, till exempel ett längre cykelpass för en löpare. Det läggs i den längsta platsen som tillåter idrotten, aldrig på huvudidrottens långpassdag, dagen före eller dagen efter den (också över veckogränsen), aldrig dagen efter ett hårt pass och inte runt ett lopp. Det är högst tre fjärdedelar av långpasset och högst idrottens andel av veckan, och minst 60 minuter, annars blir det inget. Det räknas i idrottens andel och i veckans tak som andra pass. Veckorna räknas från den första belastningsveckan, så schemat börjar med ett.
- **Löpningen har ett eget tak:** löpminuterna växer högst 10 % över den största löpveckan före, så länge platsen tillåter en annan idrott.
- **Passen i idrottens egna mallar (steg 2):** veckan fylls med löpningens mallar. När ett pass får sin idrott tar det idrottens mall på samma plats (`library.in_sport`): samma familj och steg, och varje steg exakt lika långt som löpningens, så veckans minuter står kvar. Cykelns och SkiErgens mallar kan ha fler hårda minuter, så taket på en fjärdedel över LT1 prövas igen med dem, och går veckan över stegar motorn ned som förut. Skidor och rullskidor har SkiErgens mallar, simning löpningens med idrottens namn först i titeln (*Simning: lugnt pass*) tills steg 3. Testet och loppet är desamma i alla idrotter. Se [Passbiblioteket](#passbiblioteket).
- **Zonerna och målen per idrott:** varje idrott får puls ur sitt eget test, och målet i testets belastning (`resolve.targets`): löpning fart i min/km, cykel watt och SkiErg watt och tid per 500 m (Concept2: W = 2,80 / (s/m)³, `core.sport`). Watten avrundas till hela watt. Skidor lånar SkiErgens pulszoner tills ett eget test finns (`models.BORROWS`), men inte watten, och planen säger det. En idrott utan test får zon och ansträngning, och planen säger det, så simning går på upplevd ansträngning (RPE).
- **Kapaciteten per idrott i programmet:** löpningens ur testet som väljs under *Bygger på testet* (`services.plan_capacity`). Varje annan idrott med tester har en egen lista i formuläret (`services.sport_test_list`): idrottens laktat-, maxpuls- och zontest, simningens CSS-test, det senaste förvalt, och *Inget test* (`services.sport_capacity_of`). Utan tester, eller med *Inget test*, kommer zonerna ur maxpulsen i idrotten, och simningen går på ansträngning. Ett sparat schema väljer testet med samma dag och namn som kapaciteten det sparades med, och *Inget test* när det inte hade något. Skidor har inga tester i Tröskel: utan SkiErg i schemat väljer skidorna bland SkiErgens tester, och kapaciteten sparas som SkiErgens. Ett nytt test i en av schemats idrotter erbjuder omläggning av den idrottens mål, se [Omtest och omläggning](#omtest-och-omläggning).
- **Delarna** heter som förut i löpning. I de andra idrotterna heter de *Jämnt* och *Nedvarvning* i stället för *Löpning* och *Nedjogg*, och vilan med lugn rörelse *lugnt 2 min emellan* (`text.part_line`).
- **Appen** har samma regler i TypeScript och provas mot Python-motorn: tio fall med flera idrotter (bland dem cykel och SkiErg med test, cykel ensam och skidor som huvudidrott), och titel och delar med watt och tid per 500 m för varje mall i två scheman, på svenska och engelska. Appen visar watt och tid per 500 m i passkortet. Länken bär `power_low_w` och `power_high_w` bara i mål som har watt, så ett löpschema ser ut som förut och en äldre app läser det.
- **Äldre scheman** (motor 4) har löpningens mallar också i cykelpassen. De läses som de är och visas med idrottens namn och cykelns watt.

- **Titlarna:** i ett schema med fler än en idrott, eller med styrka, börjar också löpningens pass med idrotten (*Löpning: lugnt pass*, `Plan.title`, `Workout.title_in`). Kalenderfilen och appen använder den titeln. Ett schema med bara löpning ser ut som förut.

#### Styrka

Styrka är en egen idrott utan zoner och utan test (`models.STRENGTH`, beslutet i #666 2026-11-15). Den har ingen roll eller andel: coachen anger antalet pass i veckan, 0–3 (`PlanRequest.strength_sessions`).

- **Ovanpå uthållighetsveckan:** veckan fylls först som utan styrka och ändras aldrig för den. Veckans tid, taket på 10 %, antalet pass och långpasset räknar bara uthållighetspassen (`Week.endurance`). Styrkepasset är ingen hård dag och har ingen zontid. Belastningen är minuter × 0,8.
- **Dagarna:** dagarna med en plats för styrka (en plats med idrotten `strength`, och passet får platsens klockslag). Utan sådana platser kan styrkan ligga på varje dag med en uthållighetsplats, som ett eget pass utan klockslag samma dag.
- **Aldrig** på en loppdag eller dagen före ett lopp, inte de tre dagarna före ett A- eller B-lopp och inte efter målet. Loppveckan har högst ett styrkepass.
- **Regeln för hårda dagar** (`engine._Filler._strength`): bland dagarna väljs de som i tur och ordning
  1. ligger dagen före ett hårt pass eller långpasset så få gånger som möjligt,
  2. har minst en dag emellan,
  3. har minst uthållighet själva: först en dag utan pass, sedan en dag med ett lugnt pass, sist en hård dag eller långpassets dag,
  4. kommer tidigast i veckan.
- **Skälet:** hårda dagar och långpasset står kvar där reglerna för uthållighet lagt dem, och benen kommer utvilade till dem. Det följer förslaget att styrkan ska ligga på de lättaste dagarna och aldrig dagen före ett hårt benpass eller långpasset när det går. Går det inte (till exempel tre pass på tre platser för styrka) blir det överkroppspasset som hamnar före den hårda dagen.
- **Passen:** ett pass i veckan är *helkropp*, två är *ben och bål* och *överkropp och rörlighet*, och tre är alla tre. Benpasset tar den valda dag som har mest utrymme. Nedtrappningen och loppveckan får *underhåll*: kortare, lättare och ingen tung belastning.
- **Ett missat styrkepass ersätts aldrig.** Ingen regel flyttar pass, och veckorna efter har samma antal som förut.
- **Lagringen:** `strength_sessions` och skissveckans `strength` sparas bara när de är satta, så ett schema utan styrka sparas som förut.

#### Simning

Simningen har egna mallar (se [Passbiblioteket](#passbiblioteket)) och följer upplevd ansträngning (RPE). Utan simtest visar passet zon och ansträngning, utan siffror.

- **Simtestet** är ett manuellt test utan utrustning (`assessments/css.py`): 400 m och sedan, efter full vila, 200 m, båda så fort ett jämnt tempo tillåter. CSS-tempot per 100 m är (T400 − T200) / 2. `capacity` ger planens `Capacity` för simning. Tiderna kontrolleras: 200 m mellan 1:40 och 10:00, 400 m mellan 3:20 och 20:00, och 400 m tar mer än dubbelt så lång tid som 200 m.
- **I programmet** skrivs testet in i formuläret *Simtest (CSS)* på sidan *Historik* (`ui/views/css_test.py`): tiderna, datumet och bassängens längd. Felen är texterna som `css.check` ger nycklar till. Testet sparas som ett pass utan mätvärden (`services.save_css_test`): `Session` med `kind = TestKind.SWIM_CSS`, `sport = Sport.SWIM`, inga steg och `result = css.to_result(test)`. Simning är därför en sport i `core.sport.Sport`, men den går inte att välja i guiden *Nytt test*. Historiken visar testet med tempot och öppnar det i formuläret igen. Handboken: [Simtestet (CSS)](handbok/traningsschema/traningsschema.md#simtestet-css).
- **Schemat tar det senaste testet:** i formuläret är idrottarens senaste giltiga CSS-test förvalt i simningens lista (`services.sport_test_list`), ett äldre test eller *Inget test* går att välja, och under listan står tempot. Ett nytt simtest erbjuder omläggning när det sparas, se [Omtest och omläggning](#omtest-och-omläggning).
- **Med ett simtest** får simpassen tempo per 100 m i varje zon (`resolve.SWIM_ZONES`), som sekunder runt CSS-tempot:

| Zon | Tempo per 100 m |
|---|---|
| Z1 | långsammare än CSS + 15 s |
| Z2 | CSS + 15 s till CSS + 7 s |
| Z3 | CSS + 7 s till CSS + 3 s |
| Z4 | CSS + 3 s till CSS − 2 s (tröskeln) |
| Z5 | snabbare än CSS − 2 s |

Banden följer Swim Smooths träning på CSS *(ej verifierat mot källa)*. Planen ger ingen puls i vattnet.

- **Inte byggt:** formuläret för testet i programmet och en simsport bland testerna (`catalog.SPORT_TESTS`).

#### Två pass samma dag

En veckodag kan ha en andra plats (`periodize.second_slots`): en kärnplats till samma dag, på ett annat klockslag, när de två platserna tillsammans tillåter två olika av planens idrotter. Den längsta sådana platsen används, högst en om dagen. Ett schema med en idrott har inga andra platser. I formuläret är det *+* bredvid dagen ([handboken](handbok/traningsschema/traningsschema.md#två-pass-samma-dag)).

- **Antalet pass** räknas på dagarna och de andra platserna tillsammans, inom nivåns antal pass. Dagarna tas först, så ett andra pass kommer bara när varje dag redan har ett och veckan vill ha fler. Det veckan rymmer (`periodize.week_capacity`) räknar de andra platserna som lugna pass.
- **Det andra passet är lugnt**, ett återhämtningspass på en dag med ett hårt pass, och får en mindre del av de lugna minuterna (vikten 0,7, som återhämtningen). Det läggs först på dagar utan hårt pass eller långpass, aldrig på långpassets dag, dagarna runt ett lopp eller dagarna före ett A- eller B-lopp, och inte i nedtrappningen eller loppveckan. Nyckeln slutar på `-2`.
- **Två idrotter:** dagens två pass får olika idrotter. Varje pass får sin idrott som förut (`engine._Filler._assign`), men inte den idrott dagens andra pass redan fått, och inte en som lämnar det andra utan idrott. Går det ändå inte stryks det andra passet.
- **Hårda dagar:** aldrig två hårda pass samma dag, och en dag med två pass är hård om något av dem är hårt. Styrkan räknar en dag med två pass som en hård dag när den väljer dagar.

**Inte byggt:** belastningen som tak för veckan.

### Delmål och planer utan mål

- **B-mål (delmål, seedningslopp):** en mikrotopp. Loppveckan är 25 % lättare och utan långpass, sista kvalitetspasset ligger minst tre dagar före, dagen före är högst 30 minuter och två lugna dagar följer. Blocket runt omkring flyttas inte.
- **C-mål:** ersätter ett av veckans kvalitetspass. Dagen före och dagen efter är lugna, och långpasset stryks om det skulle hamna dagen efter loppet.
- **Långa B-lopp och tidigare A-lopp** får lugna dagar efter sig, se [Återhämtning efter ett lopp](#återhämtning-efter-ett-lopp).
- **Utan tävlingsmål (3, 6 eller 12 månader):** grund och uppbyggnad i cykler om 8–12 veckor, där varje cykel slutar med ett test i stället för ett lopp. Testet är målet, och framsteget syns i nästa rapport.

### Återhämtning efter ett lopp

Efter ett lopp som schemat fortsätter efter får de närmaste dagarna bara lugna pass (`periodize.recovery_days`, `engine._Filler`). Det gäller ett A-lopp som inte är schemats mål (ett tidigare A-mål), ett långt B-lopp (delmål), och ett A- eller B-lopp strax före schemats start, till exempel förra schemats mål när coachen gör ett nytt schema och anger loppet som delmål.

- **Antalet dagar:** en dag per 3 km i löpning, avrundat uppåt: 4 dagar efter 10 km, 8 efter ett halvmaraton och 15 efter ett maraton, högst 21. Ett A-lopp utan distans ger 7 dagar. Ett B-lopp får dagarna först från 7 dagar (ett halvmaraton i löpning), annars gäller de två lugna dagarna som varje A- och B-lopp redan har. Ett C-lopp får inga.
- **Källan:** tumregeln *en lugn dag per 3–5 km* av loppet är vanlig i löpning, och en strängare variant är *en dag per engelsk mil (1,6 km)*, som brukar tillskrivas Jack Foster *(ej verifierat mot källa)*. Programmet väljer den försiktiga änden av den första, 3 km. Regeln gäller de hårda passen och långpasset, inte vila: lugn träning är tillåten.
- **Andra idrotter:** en dag per 15 km på cykel, 10 km på skidor och SkiErg och 1 km i simning. De talen är programmets egna och inte hämtade ur en källa: de är satta så att ett vanligt långlopp i idrotten ger ungefär en vecka (Vasaloppet 90 km ger 9 dagar, 150 km på cykel 10 dagar).
- **Dagarna:** inget hårt pass, inget långpass (inte heller den stödjande idrottens), inget andra pass samma dag, och varje pass högst 30 minuter den första halvan av dagarna och 45 minuter den andra. Ingen styrka den första halvan.
- **Testet:** ett omtest läggs inte i en vecka med sådana dagar, utan i nästa lätta vecka. Ett schema utan mål har testet i slutet av varje cykel, och ett lopp strax före det tar bort testet den cykeln.
- **Veckorna** med sådana dagar säger det: *Återhämtning efter Halvmaran: lugna pass, inga hårda pass och inget långpass till och med 2027-01-10* (`plan.week.recovery`). Ett lopp före schemat står i översikten i stället för raden om att loppet ligger utanför schemat (`plan.note.recovery_before`).
- **Efter schemats mål** finns inga veckor, så där behövs inget. Ett nytt schema efter målet får återhämtningen när målet anges som delmål i det nya schemat.

### Omtest och omläggning

- **Var 8–12:e vecka, i en lätt vecka,** läggs ett test hos coachen i planen. Planer utan mål får ett test i slutet av varje cykel. Planer med mål får ett test i den första lätta veckan när minst 8 veckor gått sedan förra testet, dock inte närmare målet än 4 veckor. Ett fälttest för den som inte kan komma till coachen finns inte än.
- **Efter testet (byggt):** programmet ser att idrottarens schema bygger på ett äldre laktat-, maxpuls- eller zontest och erbjuder *Lägg om schemat*, på resultatet av det nya testet och på sidan *Träningsschema* (`services.retest_candidates` och `services.apply_retest`). Passen från och med den dagen får mål ur det nya testet. Passen före har kvar målen de lämnades ut med (`Plan.capacity_log`). Inget pass byter dag, och veckor, block och passens innehåll är desamma. Omläggningen skrivs i loggen med idrotten.
- **I varje idrott (byggt, #666):** ett test erbjuds för den idrott det ger zoner (`services.test_sport`): löpningens, cykelns och SkiErgens laktat-, maxpuls- och zontest, och simningens CSS-test. Det erbjuds när det är nyare än testet som schemats mål i idrotten kommer ur (`PlanRequest.own_capacity(...).tested`), eller när idrotten inte har något test. Skidor tar SkiErgens test (`models.BORROWS`). Ett test i en idrott som schemat inte har erbjuds inte. `resolve.retest` flyttar bara testets idrott. Ett löptest blir schemats test (`session_id`), ett test i en annan idrott lämnar löptestet kvar. CSS-testet erbjuds när det sparas (`views/css_test.py`), eftersom det inte har någon resultatsida.
- **Mot schemat i rapporten (byggt, #778):** rapporten av ett nytt test har, efter jämförelsen med förra testet, delen *Mot schemat* när idrottaren har ett sparat schema med dagar mellan testerna (`services.plan_followup`, `plan/expected.py`). Den visar farten (effekten på cykeln och SkiErgen) och pulsen vid LT1 och LT2: förra testet, det schemat räknade med och det uppmätta, och om testet ligger före, efter eller som planerat. Inom 1,5 % av det väntade räknas som planerat, eftersom det är mätfelet mellan två tester. Under tabellen står hur stor andel av de planerade minuterna och hur många kvalitetspass som blev gjorda mellan testerna, per idrott när schemat har flera, och passen utanför schemat.
  - **Det schemat räknar med** är en uppskattning, och rapporten säger det. Motorn har ingen modell av idrottarens utveckling, så regeln är enkel: tröskelfarten stiger med en andel i veckan efter nivån (nybörjare 1 %, motionär 0,6 %, vältränad 0,35 %, hög volym 0,2 %), räknat bara för schemats veckor mellan testerna. En lätt vecka och en testvecka räknas till hälften, nedtrappningen till en fjärdedel och loppveckan inte alls. En stödjande idrott får halva ökningen och en idrott som bara hålls vid liv ingen. Efter 12 sådana veckor halveras ökningen. Pulsen vid trösklarna väntas ligga still. Andelarna är Tröskels egen uppskattning ur studierna som står i modulen (Londeree 1997, Jones och Carter 2000, Midgley med flera 2007, Mujika och Padilla 2003), inte siffror ur en enskild studie. Uppskattningen räknas ur schemat när rapporten görs och sparas inte, så ett sparat schema ger alltid samma förväntan.
  - **Det som blev gjort** kommer ur passens status och ur loggarna som idrottaren skickat från telefonen (händelserna `phone`, [Appen → Från idrottare till coach](app/app.md#från-idrottare-till-coach)). Den senaste loggen som markerar ett pass gäller. Ett delvis gjort pass räknas med sina loggade minuter, eller hälften av de planerade när loggen saknar minuter. Ett pass efter den senaste loggen och utan status räknas inte som missat utan som okänt, och andelen räknas bara över det som är känt. Utan logg och status står det att schemat kan läsas in från telefonen.
- **Inte byggt:** motorn bygger om resten av planen från testveckan (nya pass och ny volym), med samma mål och samma tillgänglighet, och visar skillnaden mot den gamla planen innan coachen godkänner. Gjorda veckor ändras aldrig.
- **Flyttade trösklarna sig mindre än mätfelet** behålls planen som den är. Gränsen sätts i steg P4.

### Det rullande schemat

Ett långt schema behöver följa tiden som går, utvecklingen och nya tester. Därför har bara de närmaste veckorna pass, och resten är en skiss som fylls i efter hand (`plan/rolling.py`).

- **Skissen:** hela schemat byggs som förut. Blocken, veckotyperna, veckotiden och målet ligger fast. Men bara veckan för dagen schemat byggs och de tre veckorna efter har pass (`engine.DETAIL_WEEKS`, `build_plan(…, today=)`). Veckorna efter sparas utan pass, med summorna motorn fyllde dem med: tid, antal pass, antal hårda dagar, längsta passet och tid per zon (`Week.sketch`). Loppen som idrottaren angett ligger kvar i skissveckorna, men kommer inte med i kalenderfilen förrän veckan har pass.
- **Förnya (`roll_forward(plan, today)`):** ger pass till och med tre veckor efter den vecka dagen faller i, ur det som är sparat: samma underlag, samma skiss och samma schema-id. Inget fylls i. Motorn fyller veckorna i ordning från vecka 1 på den sparade skissen och tar passen för de nya veckorna. Fyllningen tittar inte på testet, så samma schema och samma dag ger alltid samma pass. En vecka får alltså samma pass oavsett om den fick dem när schemat byggdes eller vid en förnyelse.
- **Veckor som redan har pass ändras aldrig:** de passerade veckorna och de som lämnats ut ligger kvar som de sparades, med sin status. Skissen efter den nya gränsen ligger också kvar.
- **Ett nytt test** flyttar målen i puls och fart (`resolve.retest`) och förnyar sedan samma väg (`services.apply_retest`).
- **Rena funktioner:** `roll_forward`, `skeleton_of`, `due` och `renew_soon` har inget Qt, ingen klocka (dagen kommer in som argument) och ingen slump, så att de kan flyttas till en mobilapp som de är. De är översatta till TypeScript i idrottarens app tillsammans med resten av motorn ([Appen → Mina tider och planmotorn](app/app.md#mina-tider-och-planmotorn)).
- **I programmet:** sidan *Träningsschema* säger hur långt passen går, med knappen *Förnya*. När passen tar slut inom 7 dagar erbjuds *Förnya* överst på sidan och i idrottarens formulär (`services.renew_plan`, `services.plan_renew_due`). PDF:en har ett veckoblad per vecka med pass och en sida med skissen. Kalenderfilen har bara passen.

### Passbiblioteket

Passen är data i `plan/library.py`, inte kod. Varje mall har familj, hårdaste zon och antingen en stegring (fasta upplägg, lättast först) eller en längd som motorn sätter. Texterna (namn, syfte och hur passet ska kännas) ligger i `i18n/plan.sv.json` och `plan.en.json`.

Varje pass har också en **guide** till idrottaren, som appens ruta för dagen visar:

- **Känslan:** zonen och ansträngningen av 10 för de delar som bär passet, inte uppvärmningen och nedvarvningen (`library.feel`, `text.feel_line`): *Z4, ansträngning 7 av 10*, eller *Z2–Z3, ansträngning 4–6 av 10* i ett långpass med fartökning. Styrka har bara ansträngningen, och ett lopp ingen känsla.
- **Tips:** `library.TIPS` ger varje mall noll till fyra tips (`plan.tip.<id>`), och samma tips kan stå i flera mallar, till exempel *Sista intervallet går lika fort som det första* i alla intervallpass.
- **Övningar:** teknikpasset i simning har tio övningar att välja bland, från uppvärmningen till catch-up, enarmssim och sculling (`library.DRILLS`, `plan.drill.<id>`).

Appen har samma tabeller i `app/src/engine/library.ts`, och fixturerna (`app/scripts/make_fixtures.py`) håller dem lika.

| Mall | Upplägg | Stegring |
|---|---|---|
| Lugn löpning | 25–75 min i zon 2 (upp till 1:45 på nivån *Hög volym*) | Tid |
| Återhämtningsjogg | 20–45 min i nedre delen av zon 2 (upp till en timme på nivån *Hög volym*), dagen efter ett hårt pass eller långpass | Tid |
| Långpass | Från 30 min i zon 2. I toppblocket för långa lopp med de sista 20 min i zon 3 | Högst 15 min per belastningsvecka |
| Tröskelintervaller | 4×5 min till 4×10 min i zon 4, strax under LT2 | Total arbetstid 20–40 min |
| Hårda intervaller | 4×3 min till 6×4 min i zon 5, över LT2 | Antal, sedan längd |
| Fartlek | 5×2 min till 8×3 min i zon 4 med lugn jogg emellan | Antal, sedan längd |
| Backintervaller | 6×45 s till 12×60 s | Antal, sedan längd |
| Fartpass inför loppet | 4×1 min i zon 4 | – |
| Test hos coachen | Laktattrappan: 15 min uppvärmning, 35 min test, 10 min nedjogg. De andra idrotterna har egna test, se [Testet per idrott](#testet-per-idrott) | – |
| Lopp | Målet, delmålet eller träningsloppet | – |

- **Stegringen** går ett steg per belastningsvecka som passet används. En lätt vecka och nedtrappningen går två steg tillbaka. Ett pass som inte ryms i platsen stegas ned tills det ryms.
- **Nivån** avgör var stegringen börjar, och en nybörjare får inget över LT2 de första fyra veckorna.
- **Cykel och SkiErg** har en mall på varje löpmalls plats (`library.SPORT_TEMPLATES`), med samma familj och lika många steg, och varje steg lika långt som löpningens. Uppvärmningen och intervallerna är cykelns egna, och nedvarvningen tar det som blir kvar (minst 5 minuter, `library._fit`). Målen kommer ur idrottens test: watt på cykeln, watt och tid per 500 m på SkiErgen.

| Löpmall | Cykel | SkiErg (också skidor och rullskidor) |
|---|---|---|
| Lugn löpning, återhämtning, långpass, långpass med fartökning | Lugnt pass i Z2, återhämtningspass, långpass, långpass med fartökning: samma längd och zoner som löpningens | Lugnt pass, återhämtningspass, långpass, långpass med fartökning |
| Fartlek | Sweet spot: 2×10 min till 3×12 min på 88–94 % av tröskelwatten, lätt tramp emellan | Fartlek: löpningens upplägg |
| Tröskelintervaller | 3×8 min till 3×15 min (2×20 min i steg 4) i zon 4. Total arbetstid 24–45 min | Tröskelintervaller: löpningens upplägg |
| Hårda intervaller | VO2-intervaller: 4×3 min till 6×4 min i zon 5, vila lika länge som intervallet | Hårda intervaller: löpningens upplägg |
| Backintervaller | Korta intervaller: 8 till 16 × 1 min i zon 5, 1 min lätt tramp emellan | Korta intervaller: löpningens upplägg (45–60 s) |
| Fartpass inför loppet | 3×2 min i zon 4 | Löpningens upplägg |

- **Sweet spot** anges som 88–94 % av cykelns tröskelwatt, alltså watten vid LT2 i cykeltestet (`resolve.BANDS`, `resolve.band_targets`). Med LT2 på 240 W blir det 211–226 W.
  - **Varför inte en zon:** zonernas gränser är LT1, mitten mellan trösklarna och LT2. LT1 ligger olika högt hos olika cyklister, så ingen zon motsvarar 88–94 % av tröskelwatten. Passdelen har därför ett **band** (`Part.band = "sweetspot"`) vid sidan av zonen, och bandets watt räknas ur testet när passet visas, som zonernas.
  - **Blocken räknas som zon 4** och visas med zon 4:s puls. Med LT1 på 75 % av tröskelwatten är zon 4 87,5–100 %, så bandet är zonens nedre halva.
  - **Bandet klipps inte mot zonen.** Ligger LT1 högt (över 76 % av tröskelwatten) börjar bandet något under zon 4:s watt. Texten i passet säger att watten styr.
  - **Utan uppmätt tröskelwatt** (inget cykeltest, eller bara maxpuls) visas blocken med zon 4:s puls och ansträngning.
  - **Ett omtest** flyttar bandet med tröskeln. Ett schema som sparades före motorns version 7 visas som det delades ut: blocken i zon 3 med hela zonens watt.
- **Simning** har en mall på varje löpmalls plats, på ansträngning eller med tempo per 100 m ur CSS-testet: *lugnt pass*, *teknikpass* (teknikövningar i lugnt tempo, på återhämtningens plats), *långpass*, *intervaller* (8×3 min till 6×8 min strax under CSS med 30–60 s vila), *hårda intervaller* (10×1 min till 10×3 min), *växlande tempo* (på fartlekens plats), *korta intervaller* (8 till 24 × 30 s) och *fartpass inför loppet*.
- **Styrka** har fyra mallar utan zoner: *helkropp* (45 min), *ben och bål* (40 min), *överkropp och rörlighet* (35 min) och *underhåll* (30 min). Varje mall har uppvärmning, styrka och rörlighet med ansträngning, se [Styrka](#styrka).
- **Inte byggt:** gång-löpning för nybörjare.

#### Testet per idrott

Testet i en testvecka görs i huvudidrotten och har idrottens eget upplägg (`library._test`). Alla är 60 minuter, som löpningens, så testveckan har samma tid oavsett idrott. Testdelen har ingen zon och räknas som zon 4 i belastningen.

| Idrott | Pass | Uppvärmning, test, nedvarvning | Testet |
|---|---|---|---|
| Löpning | *Test hos din coach* | 15, 35, 10 min | Laktattrappan på bandet |
| Cykel | *Cykel: laktattest hos din coach* | 10, 40, 10 min | Laktattrappa på trainern: 6–8 steg på 5 min, 25–30 W mer per steg |
| SkiErg | *SkiErg: laktattest hos din coach* | 10, 35, 15 min | Laktattrappa på SkiErgen: 6–8 steg på 4 min, 15–20 W mer per steg, en halv minuts paus för provet |
| Skidor | *Skidor: laktattest på SkiErg hos din coach* | 10, 35, 15 min | Samma test som SkiErgens. Skidor har inget eget test och lånar SkiErgens zoner |
| Simning | *Simning: simtest, 400 m och 200 m* | 20, 25, 15 min | CSS-testet: 400 m och, efter full vila, 200 m i jämn högsta fart. Idrottaren simmar själv och lämnar tiderna till coachen (`assessments/css.py`) |

- **Uppläggen följer programmets tester** (`assessments/library_power.py` och texterna i *Om testerna*). Ändras stegen där ska passets text i `plan.workout.<idrott>_test.how` ändras också.
- **Ett schema sparat före motorns version 7** har löpningens testmall i alla idrotter och visas som förut (*Cykel: test hos din coach*).
- **Inte byggt:** ramptest och FTP-test som testpass, och ett fälttest för den som inte kan komma till coachen.

### Var en språkmodell kan hjälpa senare

Inte i motorn. Målen och passen sätts alltid av reglerna, aldrig av en språkmodell. Möjliga uppgifter senare:

- svara på frågor i online coaching ur planen och dess skäl ("varför är torsdagen lugn?"),
- föreslå en ändring som coachen godkänner och som sedan körs genom motorn,
- skriva veckans sammanfattning i löpande text.

Hälsodata till en språkmodell kräver ett eget samtycke och ett biträdesavtal. Stravas villkor tillåter inte att Stravas data ges till en AI-modell.

## 3. Säkerhet och ansvar

- **Hälsodeklaration före start (byggd i enkel form):** sidan *Träningsschema* har tre rutor som coachen bockar i med idrottaren: frågorna om hjärta, bröstsmärta, yrsel, läkemedel, graviditet och skador är besvarade, inget svar hindrar träningen eller en läkare har sagt ja, och idrottaren vet att schemat är ett träningsråd. Utan alla tre går schemat inte att skriva ut, spara som PDF eller som kalenderfil, eller mejla. Rutorna sparas med datum (`plans.health_json`) och gäller i 12 månader. Inte byggt: svaren fråga för fråga i stil med PAR-Q+ *(namn och innehåll ej verifierat)*, och ett eget formulär som idrottaren skriver under.
- **Maxtester kräver mer:** samma deklaration före varje maxtest, och coachen ser den i guiden innan testet går att starta.
- **Försiktig start för nybörjare:** inga pass över LT2 de första fyra veckorna, start på låg volym, 2+1 i stället för 3+1 och gång-löpning i passbiblioteket.
- **Texten på varje plan och i tjänsten:** planen är ett träningsråd byggt på ett test, inte sjukvård. Den ersätter inte läkare eller fysioterapeut. Vid bröstsmärta, yrsel eller onormal andfåddhet avbryts passet och vård kontaktas. Den exakta texten bör läsas av någon som kan juridiken.
- **Sjukdom:** feber eller symtom nedanför halsen ger vila. Efter sjukdom lika många lugna dagar som sjukdagar, och det första kvalitetspasset tidigast när ett lugnt pass känts normalt *(tumregler, ej verifierat)*.
- **Missade pass:** ett missat pass tas inte igen genom att läggas ovanpå ett annat. Ett kvalitetspass får flyttas till en lugn dag om det inte hamnar bredvid ett annat, annars stryks det. Efter en missad vecka upprepas veckan, och efter två eller fler backar planen ett steg i volym.
- **Systemet sänker, det höjer inte av sig självt.** Höjningar görs vid omtest eller av coachen. Regeln gör planen säker att lämna ifrån sig.

## 4. Utskriften

Utskriften byggs i rapportmotorn (`report/plan_builder.py` och mallen `plan.html.j2`: Jinja2 till HTML till PDF), med coachens logga och samma typografi som testrapporten. Zonerna har klockans färger ur `core/zone_colors.py`.

| Sida | Innehåll |
|---|---|
| **Översikten** (sida 1) | Målet och delmålen, perioden, testet schemat bygger på, tillgänglig tid, fyra nyckeltal, blocken som ett band över en stapel per vecka (tid per zon, med lätt vecka, test och lopp utmärkta) och zonerna med puls och fart (watt på cykel, watt och tid per 500 m på SkiErg) |
| **Så läser du schemat** (1–2 sidor, upp till 3 med flera idrotter) | Rutan *Så använder du schemat* och schemats anmärkningar, blocken med skälen, zontabellen, passen som schemat använder med syfte och känsla, reglerna bakom schemat och ansvarstexten |
| **Veckoblad** (en sida per vecka med pass) | Sju dagar: tid, passets namn och syfte, delarna med zon, puls och fart, en ruta att bocka av och rader att anteckna på. Dagens pass i tidsordning. Veckans syfte överst, och nederst veckans tid per zon och belastningen (`Week.load`) |

- **Passen i schemat** (`plan_builder._guide`) tar varje mall som schemat använder, i en grupp per idrott som har egna mallar: löpning, cykel (också sweet spot), SkiErg, simning och styrka, och idrottens eget test. Grupperna står i idrotternas ordning med huvudidrotten först och har idrotten som rubrik. Skidor använder SkiErgens mallar och står i dess rubrik. Ett schema med bara löpning har en grupp utan rubrik.
- **Sidbrytningarna:** rutan *Så använder du schemat* och anmärkningarna står överst i läsanvisningen och inte på översikten, där de inte rymdes och hamnade ensamma på en egen sida. Ansvarstexten hålls ihop med reglerna. Provet `tests/report/test_report_plan_guide.py` läser PDF:en sida för sida.
- **Titlarna** kommer ur `Plan.title`, så i ett schema med flera idrotter eller med styrka börjar varje pass med idrotten.
- **Styrka och simning:** styrkepassen står på veckobladet utan zon, med ansträngningen, i styrkans färg, och sist i idrottstabellen med antalet pass i veckan. Simningen med ett CSS-test får tid per 100 m i passen och en rad under *Dina zoner*. Veckans tid i veckohuvudet är uthållighetstiden; stapeln per idrott och belastningen tar med styrkan.
- **Flera idrotter:** idrottstabellen på översikten (roll, andel som procent av andelarnas summa, och testet med datum, eller att skidor lånar SkiErgens zoner och att simning går på ansträngning) i stället för raden *Bygger på*. Under *Dina zoner* en rad per annan idrott vars test ger watt. En färg per idrott (`core.zone_colors.sport_color`, ingen av dem en zonfärg) bredvid passets namn i stället för zonfärgen, och veckans tid per idrott som en stapel på veckobladet. Resten av översikten ritas lite mindre, så att den ryms på en sida.
- **Skissen** (sist, bara i ett rullande schema): veckorna utan pass, en rad var med block, typ av vecka, tid och antal pass. I översikten är skissveckornas staplar bleka.
- **Kalenderfilen** (`plan/ics.py`) har en händelse per pass i veckorna med pass, med passets delar och mål i beskrivningen. En plats med klockslag ger en händelse vid den tiden, annars en heldagshändelse. Händelsens id är passets fasta nyckel, så att en ny import skriver över i stället för att dubblera.
- **Inte byggt:** passkort (ett pass per halvsida) och utskrift av ett block i taget.
- **Veckobladet på en sida:** ett blad ryms på en A4-sida också med flera idrotter och styrka. Tabellen är tät, foten har veckans tid per zon och per idrott bredvid varandra, och en vecka med åtta rader eller fler (två pass någon dag) ritas med tabellen i 92 % (`.days.dense` i `plan.css`). En vecka som ändå inte ryms bryts inne i tabellen mellan två pass, med kolumnrubrikerna upprepade: huvudet och foten står aldrig ensamma på en sida. Provet `tests/report/test_report_plan_week_sheets.py` läser PDF:en sida för sida på svenska och engelska.
- Utskriften på Windows är inte provad än ([PROV.md](PROV.md)).
