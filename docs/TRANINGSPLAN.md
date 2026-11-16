# Träningsplanen

*Hur programmet bygger ett träningsschema på testerna: datamodellen, planmotorn, säkerheten och utskriften. Planmotorn, utskriften, kalenderfilen, lagringen och sidan *Träningsschema* i programmet är byggda (se [Nuläget](#nuläget-vad-som-är-byggt)). Motorn fyller flera idrotter med löpningens pass; egna pass och mål per idrott, styrka och formuläret för idrotterna är inte byggda. Målet är ett komplett test- och träningsprogram där idrottaren vet vad varje pass ska innehålla, och där träningen är skräddarsydd efter idrottarens kapacitet och mål.*

Allt som är märkt *(ej verifierat)* är skrivet ur minnet.

<!-- toc -->
## Innehåll

- [Sammanfattning](#sammanfattning)
- [Nuläget: vad som är byggt](#nuläget-vad-som-är-byggt)
- [1. Datamodellen](#1-datamodellen)
- [2. Planmotorn](#2-planmotorn)
  - [Blocken](#blocken)
  - [Intensitetsfördelningen](#intensitetsfördelningen)
  - [Flera idrotter](#flera-idrotter)
  - [Delmål och planer utan mål](#delmål-och-planer-utan-mål)
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
| Indata: idrottare med kapacitet per sport, idrotter med roll och andel, tillgänglighet per veckodag, mål (A, B, C) eller längd, nivå och nuvarande veckovolym | Byggt i motorn. Formuläret har bara löpning än |
| Block bakåt från målet, lätta veckor, omtest, volymökning, långpass, kvalitetspass med vila emellan | Byggt, se [Planmotorn](#2-planmotorn) |
| Passbibliotek för löpning, cykel och SkiErg med texter på svenska och engelska | Byggt. Skidor har SkiErgens pass, simning löpningens med idrottens namn i titeln, se [Passbiblioteket](#passbiblioteket) |
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
| Cykelns och SkiErgens egna pass med mål i watt och tid per 500 m | Byggt (steg 2 av 4) |
| Styrka, simtest, idrotterna i formuläret och utskriften, återhämtningsblock efter ett mål, passkort | Inte byggt |

## 1. Datamodellen

Dataklasser utan ORM, som resten av programmet (`core/models.py`). De ligger i `plan/models.py`. Sporten är värdet ur `core/sport.py` (`RUNNING = Sport.RUN.value`, alltså `"run"`). Scheman som sparades med den gamla nyckeln `"running"` läses som `"run"` (`plan/codec.py`).

```
PlanRequest                      det idrottaren och coachen anger
  athlete_name, start: date      schemat börjar första måndagen från och med start
  availability: [Slot]
  capacity: [Capacity]           en per sport
  sports: [str]                  idrotterna när roles är tomt; den första (eller A-målets) är huvudidrott
  roles: [SportRole]             idrotterna med roll och andel; tomt i scheman sparade före flera idrotter
  goals: [Goal]                  tomt = plan utan tävlingsmål
  duration_weeks: int | None     längden när inget A-mål finns (förval 12)
  level: "beginner" | "recreational" | "trained"
  recent_weekly_minutes          snitt de senaste 4–6 veckorna, om känt

Slot                             ett tillfälle i veckan
  weekday, max_minutes, start: time | None
  sports: [str]                  vad som går att träna då
  role: "core" | "extra"         bara kärnplatser fylls

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
  kind: "warmup" | "steady" | "work" | "cooldown" | "test" | "race"
  duration_s, repeat, rest_s, rest_zone
  zone: str | None               zonkod i Garmins fem zoner, t.ex. "Z4"
  rpe: int                       ansträngning 1–10, visas alltid

Note                             en text som nyckel och värden, så att planen går att skriva ut på båda språken
```

`Plan.capacity_log` håller de tidigare testerna efter ett omtest: en dag och kapaciteten som gällde före den. `Plan.capacity_on(sport, dag)` ger kapaciteten ett pass visas med.

`PlanRequest.plan_roles()` ger idrotterna i ordning med huvudidrotten först, en gång var och med exakt en huvudidrott: den första som angetts som huvudidrott, annars A-målets idrott, annars den första. `Workout.load` och `Week.load` är belastningen: minuter × zonfaktor (Z1 0,5, Z2 1, Z3 1,5, Z4 2,5, Z5 3,5; ett test räknas som Z4) × idrottsfaktor (löpning, SkiErg och skidor 1,0, cykel och simning 0,7, styrka 0,8).

**Inte i modellen än:** utrustning per plats och gruppträningar med fast innehåll.

**Tre regler i modellen:**

1. **Passdelen har en zon, inte en puls.** Målpuls och målfart räknas ur `Capacity` när passet visas, skrivs ut eller exporteras (`plan/resolve.py`). Fart anges bara där testet mätte den, alltså vid trösklarna och mellan dem. Saknar idrottaren ett test visas bara zonens namn och ansträngningen, och med bara maxpuls märks zonerna som uppskattade. Efter testet *Puls- och fartzoner* är zonerna uppskattade ur maxpulsen från trappan, och varje gräns får en fart ur sambandet mellan puls och fart i stegen (`hr_zones.zone_speeds`, samma som resultatsidan): lite utanför stegen märkt *≈*, längre ut ingen fart. Ett zontest som stoppades innan trappan nådde en minut ger ingen maxpuls (`services.plan_capacity`).
2. **Planen pekar på testerna den bygger på** (`based_on`). `resolve.retest()` ger samma plan på ett nytt test: zonerna ligger kvar och målen följer de nya trösklarna, från en angiven dag eller i hela planen.
3. **Varje block och varje ändring har ett skäl i klartext** (`reason`). Det är det som gör planen förklarlig.

**Lagring:** migrering 9 i `storage/db.py`, efter huvudgrenens version 8 (sporten), lägger till två tabeller och ändrar inget annat. `tests/storage/test_plan_migration.py` bygger en databas i version 8 med e-post, klockans VO2max, ett cykeltest och maxpuls per sport, och visar att allt finns kvar efter migreringen och att kopian är kvar i version 8.

| Tabell | Innehåll |
|---|---|
| `plans` | En rad per idrottare (`athlete_id` är unik): `session_id` (testet schemat bygger på), `request_json` (tillgänglighet, mål, delmål, längd, nivå, veckovolym och testets trösklar), `plan_json` (schemat som det byggdes: block, veckor, pass och loggen över tidigare tester), `health_json` (hälsodeklarationens rutor och datum), `engine_version`, `created`, `updated` |
| `plan_events` | Loggen: tid, händelse (`saved`, `changed`, `retest`, `renewed`) och detaljer, till exempel vilka två tester ett omtest gick mellan |

- **Schemat sparas som det byggdes** (`plan/codec.py` gör om det till JSON och tillbaka). Det byggs inte om när det läses, så ett sparat schema ändras inte av en ny programversion. `engine_version` säger vilka regler som byggde det, eller som senast förnyade det. Formatet är 2: `horizon` och skissveckorna (`sketch`) sparas med schemat. Ett schema i format 1 saknar dem och läses som ett schema där alla veckor har pass. Idrotternas roller sparas under `roles` i det som angavs. Ett schema som sparades före dem saknar fältet och läses utan roller, alltså med idrotterna i `sports`.
- **Namnet sparas inte i schemat.** Det läses från idrottarens rad när schemat öppnas.
- **Målen följer testet.** När schemat öppnas räknas trösklarna om ur testet det bygger på, så att ett rättat laktatvärde slår igenom. Är testet borttaget gäller trösklarna som sparades med schemat.
- **Radering:** raderna följer idrottaren (`ON DELETE CASCADE`) och skrivs över som övriga uppgifter. Tas testet bort ligger schemat kvar utan test. Schemat är med i utdraget (*Exportera allt*). PDF:en och kalenderfilen registreras som sparade filer, så att raderingen hittar dem.

## 2. Planmotorn

Modulen `src/lopanalys/plan/`:

| Fil | Innehåll |
|---|---|
| `models.py` | Dataklasserna i [datamodellen](#1-datamodellen) |
| `library.py` | Passbiblioteket som data: löpningens pass, som alla idrotter använder tills vidare |
| `periodize.py` | Block, veckotyper och veckovolym: var den börjar, vad veckan rymmer och hur fort den växer |
| `engine.py` | `build_plan()`: passen läggs i platserna |
| `resolve.py` | Zon till puls och fart (fart bara för löpning), och `retest()` |
| `text.py` | Passen som text på aktuellt språk, för utskrift och kalender, och översiktens rad om var tiden börjar |
| `rolling.py` | Det rullande schemat: `roll_forward` ger nästa veckor pass ur den sparade skissen |
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
- **Volymen** börjar på den kända veckovolymen (*Tränar nu*). Är den okänd börjar den på nivåns förval (2, 3 eller 5 timmar), men högst på 70 % av det veckan rymmer, så att schemat aldrig börjar vid taket. **Det veckan rymmer** är nivåns antal pass i de längsta kärnplatserna, med långpasset inom nivåns längsta långpass och de andra passen inom längden på ett lugnt pass, 75 min (`periodize.week_capacity`).
- **Ökningen** sprids över grund och uppbyggnad: varje belastningsvecka växer med den takt som når hela veckan vid den sista belastningsveckan före toppen, men aldrig fortare än nivåns 5, 7 eller 8 %. Ett schema som börjar nära taket växer alltså långsamt, och ett som börjar långt under växer i nivåns takt så många veckor som behövs, även om taket aldrig nås. En vecka är aldrig mer än 10 % större än den största veckan före, och aldrig större än kärnplatserna rymmer. Startpunkten och var tiden växer till står i översikten (`plan.text.start_line`).
- **Översikten visar progressionen:** under staplarna med tid per zon ritas det längsta passet och minuterna i zon 3–5 per vecka, i en egen panel med egen axel i minuter (ingen andra axel på staplarna). Där syns långpassets ökning och kvalitetspassens steg även när veckotiden står still. Tioprocentsregeln är en tumregel med svagt stöd i forskningen *(ej verifierat)*, så den används som ett tak och inte som ett mål.
- **Långpasset** ligger i veckans längsta plats, är ungefär en tredjedel av veckan och växer högst 15 minuter från en belastningsvecka till nästa.
- **För kort tid till målet:** blocken kortas i ordningen grund, uppbyggnad, topp. Är det mindre än 6 veckor kvar säger planen det i klartext och bygger ingen topp.
- **Inte byggt:** återhämtningsblocket efter ett A-mål, och två och en för idrottare över 50 år.

### Intensitetsfördelningen

**Pyramidal fördelning i grundblocket och mer polariserad i uppbyggnad och topp.** Mätt i tid ligger cirka 80 % under LT1, alltså i zon 1 och 2 i Garmins fem zoner, som är programmets förval. I exempelschemat är det 87 %.

- **Varför inte strikt 80/20 för alla:** fördelningen kommer ur studier av elitidrottare med stor volym (Seiler). En motionär med 4–6 timmar i veckan får för lite belastning om allt över LT1 stryks, och tröskelnära träning ger mycket på få timmar *(ej verifierat mot källa)*.
- **Regeln räknas i pass, inte bara i tid:** högst två kvalitetspass i veckan, ett vid färre än fyra pass och i grundblocket, aldrig två dagar i rad (inte heller över veckogränsen), och aldrig dagen före ett långpass.
- **Högst en fjärdedel av veckans tid över LT1.** Blir det mer stegar motorn ned kvalitetspasset eller stryker det. Den stegar aldrig upp för att fylla en vecka.
- **Zonen mellan LT1 och LT2** används medvetet (tröskelintervaller), inte av misstag. Lugna pass har ett tak strax under LT1, och det taket är det viktigaste testet ger en motionär.

### Flera idrotter

Idrottaren anger idrotterna med en roll och en andel av veckotiden (`PlanRequest.roles`, beslutet i #666 2026-11-15). Utan roller gäller idrotterna i `sports`, och ett schema med en idrott blir detsamma som förut. Formuläret i programmet har inte idrotterna än (steg 4).

- **Rollerna:** *huvudidrott*, *stödjande* och *underhåll*, med andelen 60, 30 och 10 % som förval. Andelarna är vikter: 60 och 30 ger två tredjedelar och en tredjedel.
- **Platserna:** en kärnplats används när den tillåter någon av planens idrotter. En plats för en idrott som planen inte har lämnas tom. Långpassets plats är huvudidrottens längsta plats.
- **Veckan fylls som förut, över alla idrotter på en gång.** Antalet pass, de hårda dagarna, långpasset, taket på 10 % i veckan och taket på en fjärdedel av tiden över LT1 räknas alltså över idrotterna: en hård dag är en hård dag oavsett idrott, aldrig två i rad (inte heller över veckogränsen) och aldrig dagen före långpasset.
- **Sedan får varje pass sin idrott** (`engine._Filler._sports`): långpasset och testet i huvudidrotten, de hårda passen i datumordning och de lugna med det längsta först, var och ett till den idrott som ligger längst efter sin andel av veckans minuter bland de idrotter platsen tillåter. Ett lopp är i målets idrott.
- **Närmare målet** tar huvudidrotten hälften av de andra idrotternas andel i toppen och tre fjärdedelar i nedtrappningen (`engine.SHARE_SHIFT`).
- **Underhållsidrotten** får högst ett pass i veckan och aldrig ett hårt.
- **Löpningen har ett eget tak:** löpminuterna växer högst 10 % över den största löpveckan före, så länge platsen tillåter en annan idrott.
- **Passen i idrottens egna mallar (steg 2):** veckan fylls med löpningens mallar. När ett pass får sin idrott tar det idrottens mall på samma plats (`library.in_sport`): samma familj och steg, och varje steg exakt lika långt som löpningens, så veckans minuter står kvar. Cykelns och SkiErgens mallar kan ha fler hårda minuter, så taket på en fjärdedel över LT1 prövas igen med dem, och går veckan över stegar motorn ned som förut. Skidor och rullskidor har SkiErgens mallar, simning löpningens med idrottens namn först i titeln (*Simning: lugnt pass*) tills steg 3. Testet och loppet är desamma i alla idrotter. Se [Passbiblioteket](#passbiblioteket).
- **Zonerna och målen per idrott:** varje idrott får puls ur sitt eget test, och målet i testets belastning (`resolve.targets`): löpning fart i min/km, cykel watt och SkiErg watt och tid per 500 m (Concept2: W = 2,80 / (s/m)³, `core.sport`). Watten avrundas till hela watt. Skidor lånar SkiErgens pulszoner tills ett eget test finns (`models.BORROWS`), men inte watten, och planen säger det. En idrott utan test får zon och ansträngning, och planen säger det, så simning går på upplevd ansträngning (RPE).
- **Delarna** heter som förut i löpning. I de andra idrotterna heter de *Jämnt* och *Nedvarvning* i stället för *Löpning* och *Nedjogg*, och vilan med lugn rörelse *lugnt 2 min emellan* (`text.part_line`).
- **Appen** har samma regler i TypeScript och provas mot Python-motorn: tio fall med flera idrotter (bland dem cykel och SkiErg med test, cykel ensam och skidor som huvudidrott), och titel och delar med watt och tid per 500 m för varje mall i två scheman, på svenska och engelska. Appen visar watt och tid per 500 m i passkortet. Länken bär `power_low_w` och `power_high_w` bara i mål som har watt, så ett löpschema ser ut som förut och en äldre app läser det.
- **Äldre scheman** (motor 4) har löpningens mallar också i cykelpassen. De läses som de är och visas med idrottens namn och cykelns watt.

**Inte byggt (steg 3–4):** styrka som egna pass (0–3 i veckan, aldrig ersatta), simning och ett manuellt simtest (CSS); belastningen i utskriften och som tak för veckan; idrotterna i formuläret, en färg per idrott och idrottstabellen i PDF:en. Inte heller två pass samma dag (en plats per veckodag gäller) eller ett mindre långpass i en stödjande idrott.

### Delmål och planer utan mål

- **B-mål (delmål, seedningslopp):** en mikrotopp. Loppveckan är 25 % lättare och utan långpass, sista kvalitetspasset ligger minst tre dagar före, dagen före är högst 30 minuter och två lugna dagar följer. Blocket runt omkring flyttas inte.
- **C-mål:** ersätter ett av veckans kvalitetspass. Dagen före och dagen efter är lugna, och långpasset stryks om det skulle hamna dagen efter loppet.
- **Utan tävlingsmål (3, 6 eller 12 månader):** grund och uppbyggnad i cykler om 8–12 veckor, där varje cykel slutar med ett test i stället för ett lopp. Testet är målet, och framsteget syns i nästa rapport.

### Omtest och omläggning

- **Var 8–12:e vecka, i en lätt vecka,** läggs ett test hos coachen i planen. Planer utan mål får ett test i slutet av varje cykel. Planer med mål får ett test i den första lätta veckan när minst 8 veckor gått sedan förra testet, dock inte närmare målet än 4 veckor. Ett fälttest för den som inte kan komma till coachen finns inte än.
- **Efter testet (byggt):** programmet ser att idrottarens schema bygger på ett äldre laktat-, maxpuls- eller zontest och erbjuder *Lägg om schemat*, på resultatet av det nya testet och på sidan *Träningsschema* (`services.retest_candidate` och `services.apply_retest`). Passen från och med den dagen får mål ur det nya testet. Passen före har kvar målen de lämnades ut med (`Plan.capacity_log`). Inget pass byter dag, och veckor, block och passens innehåll är desamma. Omläggningen skrivs i loggen.
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

| Mall | Upplägg | Stegring |
|---|---|---|
| Lugn löpning | 25–75 min i zon 2 | Tid |
| Återhämtningsjogg | 20–45 min i nedre delen av zon 2, dagen efter ett hårt pass eller långpass | Tid |
| Långpass | Från 30 min i zon 2. I toppblocket för långa lopp med de sista 20 min i zon 3 | Högst 15 min per belastningsvecka |
| Tröskelintervaller | 4×5 min till 4×10 min i zon 4, strax under LT2 | Total arbetstid 20–40 min |
| Hårda intervaller | 4×3 min till 6×4 min i zon 5, över LT2 | Antal, sedan längd |
| Fartlek | 5×2 min till 8×3 min i zon 4 med lugn jogg emellan | Antal, sedan längd |
| Backintervaller | 6×45 s till 12×60 s | Antal, sedan längd |
| Fartpass inför loppet | 4×1 min i zon 4 | – |
| Test hos coachen | Laktattrappan | – |
| Lopp | Målet, delmålet eller träningsloppet | – |

- **Stegringen** går ett steg per belastningsvecka som passet används. En lätt vecka och nedtrappningen går två steg tillbaka. Ett pass som inte ryms i platsen stegas ned tills det ryms.
- **Nivån** avgör var stegringen börjar, och en nybörjare får inget över LT2 de första fyra veckorna.
- **Cykel och SkiErg** har en mall på varje löpmalls plats (`library.SPORT_TEMPLATES`), med samma familj och lika många steg, och varje steg lika långt som löpningens. Uppvärmningen och intervallerna är cykelns egna, och nedvarvningen tar det som blir kvar (minst 5 minuter, `library._fit`). Målen kommer ur idrottens test: watt på cykeln, watt och tid per 500 m på SkiErgen.

| Löpmall | Cykel | SkiErg (också skidor och rullskidor) |
|---|---|---|
| Lugn löpning, återhämtning, långpass, långpass med fartökning | Lugnt pass i Z2, återhämtningspass, långpass, långpass med fartökning: samma längd och zoner som löpningens | Lugnt pass, återhämtningspass, långpass, långpass med fartökning |
| Fartlek | Sweet spot: 2×10 min till 3×12 min i övre delen av zon 3, lätt tramp emellan | Fartlek: löpningens upplägg |
| Tröskelintervaller | 3×8 min till 3×15 min (2×20 min i steg 4) i zon 4. Total arbetstid 24–45 min | Tröskelintervaller: löpningens upplägg |
| Hårda intervaller | VO2-intervaller: 4×3 min till 6×4 min i zon 5, vila lika länge som intervallet | Hårda intervaller: löpningens upplägg |
| Backintervaller | Korta intervaller: 8 till 16 × 1 min i zon 5, 1 min lätt tramp emellan | Korta intervaller: löpningens upplägg (45–60 s) |
| Fartpass inför loppet | 3×2 min i zon 4 | Löpningens upplägg |

- **Sweet spot** ligger i övre delen av zon 3 i klockans fem zoner, alltså mellan trösklarna men under zonen strax under LT2. Det brukar beskrivas som 88–94 % av FTP. Planen anger hela zonens watt.
- **Inte byggt:** gång-löpning för nybörjare, styrka och rörlighet, och egna mallar för simning (steg 3). Ett test i en annan idrott än löpning är fortfarande *Test hos din coach* utan upplägg.

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
| **Översikten** (sida 1) | Målet och delmålen, perioden, testet schemat bygger på, tillgänglig tid, fyra nyckeltal, blocken som ett band över en stapel per vecka (tid per zon, med lätt vecka, test och lopp utmärkta) och zonerna med puls och fart |
| **Så läser du schemat** (1–2 sidor) | Blocken med skälen, zontabellen, passen som schemat använder med syfte och känsla, reglerna bakom schemat och ansvarstexten |
| **Veckoblad** (en sida per vecka med pass) | Sju dagar: tid, passets namn och syfte, delarna med zon, puls och fart, en ruta att bocka av och rader att anteckna på. Veckans syfte överst och veckans tid per zon nederst |

- **Skissen** (sist, bara i ett rullande schema): veckorna utan pass, en rad var med block, typ av vecka, tid och antal pass. I översikten är skissveckornas staplar bleka.
- **Kalenderfilen** (`plan/ics.py`) har en händelse per pass i veckorna med pass, med passets delar och mål i beskrivningen. En plats med klockslag ger en händelse vid den tiden, annars en heldagshändelse. Händelsens id är passets fasta nyckel, så att en ny import skriver över i stället för att dubblera.
- **Inte byggt:** passkort (ett pass per halvsida) och utskrift av ett block i taget.
- Utskriften på Windows är inte provad än ([PROV.md](PROV.md)). Sidbrytningarna i veckobladen väntar på det provet.
