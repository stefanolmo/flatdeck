# FlatDeck

<p align="center"><img src="docs/splash.png" alt="FlatDeck - schermata iniziale" width="640"></p>

Da **Gerber** ed **Excellon** a **G-code GRBL 1.1** per fresare circuiti stampati con una CNC (pensato per la CNC 1610 Pro).
Alternativa moderna e minimale a FlatCAM, che non gira più sulle versioni recenti di Ubuntu (Python 2 / PyQt4).
Stesso stile e stesso stack di [CNC Deck](https://github.com/stefanolmo/cncgrbl): Python 3 + Qt6 (PySide6), `numpy`, `pyclipper`.
Il G-code prodotto si carica poi in CNC Deck (o Candle, UGS…) per l'invio alla macchina.

<p align="center"><img src="docs/main.png" alt="FlatDeck - isolamento, foratura e contorno" width="900"></p>

## Installazione (Ubuntu)

```bash
./install.sh          # venv in ~/.local/share/flatdeck, launcher "flatdeck", voce nel menu
flatdeck examples/demo_copper.gbr examples/demo_outline.gbr examples/TrafficLight_drill.txt
```

Senza installare: `pip install -r requirements.txt && python3 -m flatdeck`.
Opzioni: `--no-splash` (o `FLATDECK_NO_SPLASH=1`) salta la schermata iniziale di 3 secondi.

## Flusso di lavoro

1. **Apri** i file (anche più insieme, o trascinali sulla finestra): rame, contorno, foratura. Il tipo si riconosce dal contenuto;
   con il tasto destro su un livello puoi forzarlo a *Rame* o *Contorno*.
2. Per il rame **inferiore** usa **Specchia X** (agisce su tutto il progetto, così i livelli restano allineati con i fori).
3. Scegli l'**origine** (predefinita: angolo basso-sinistro dei dati). È il punto che dovrai azzerare sulla macchina (X0 Y0).
4. Nelle schede **Isolamento / Foratura / Contorno** imposta i parametri (vengono ricordati) e premi *Genera*: ogni lavoro compare
   nell'elenco dei livelli con anteprima del percorso (la fascia chiara è la larghezza dell'utensile) e il G-code in basso.
5. **Salva G-code…** con il lavoro selezionato.

| Operazione | Cosa fa |
|---|---|
| Isolamento | offset del rame con `pyclipper`; passate multiple con sovrapposizione; esterna / interna / entrambe; fresa cilindrica o **punta a V** (il diametro di taglio si calcola da punta, angolo e profondità); percorsi ordinati per vicinanza |
| Foratura | un lavoro per file, utensili selezionabili e ordinati per diametro, **pausa M0 a ogni cambio punta**, foratura a passi opzionale, asole (G85 e fresate) |
| Contorno | dal livello contorno (profili chiusi concatenati, interni inclusi) oppure dal rettangolo dei dati + margine; passate a profondità crescente; **linguette** (2 o 4) per non staccare la scheda |
| Trasformazioni | specchia X/Y, ruota 90°, tutte sull'intero progetto |

G-code: solo `G0 G1 G4 G21 G90 G94 M0 M2 M3 M5`, righe < 80 caratteri, avanzamento scritto solo quando cambia; il mandrino parte
con `M3 S…` (S = 0 per non emetterlo). La Z viene azzerata **sulla superficie del rame**: profondità sempre positive nell'interfaccia.

## Limiti della v1 (cosa NON c'è, rispetto a FlatCAM)

- Niente rimozione del rame (NCC), riempimento (paint), doppia faccia con fori di allineamento, autolivellamento, invio alla macchina.
- Dopo un cambio punta **devi riazzerare Z** (nessun probing automatico).
- Gerber: non supportati i blocchi di apertura (`AB`), le trasformazioni di apertura (`LM/LR/LS`, ignorate con avviso), la macro *moiré*,
  le coordinate incrementali. Le apertura non rotonde su tracciati sono approssimate con l'inviluppo convesso. Le macro (KiCad, Eagle,
  Altium) e step & repeat sono supportate.
- Excellon: i pattern ripetuti (`R`) sono ignorati con avviso. Senza `TZ/LZ` nell'intestazione si assume che gli zeri finali siano
  omessi (conta solo se i numeri hanno meno cifre del formato).
- La generazione è sincrona: con molte passate su schede grandi l'interfaccia può restare ferma qualche secondo.

**Verifica sulla macchina reale**: i test controllano geometria e formato del G-code, non il comportamento della fresa. Il primo
lavoro va fatto con la fresa alzata (o senza rame) e con la mano sull'emergenza.

## Esempi

`examples/TrafficLight_drill.txt` è un file Excellon **reale** (56 fori, 4 punte, pollici 2.4). `demo_copper.gbr` e `demo_outline.gbr`
sono **sintetici** (generati da `examples/make_demo.py` sulle posizioni dei fori): servono solo a provare l'isolamento e il contorno,
non sono il rame vero di quella scheda.

## Struttura

```
flatdeck/geom.py        poligoni su Clipper: unione, differenza, offset, tratto con penna tonda, ordinamento percorsi
flatdeck/gerber.py      parser RS-274X: aperture, macro, archi G74/G75, regioni, polarità, step & repeat; catene del contorno
flatdeck/excellon.py    parser Excellon: unità, formato/zeri, fori, asole
flatdeck/cam.py         isolamento, foratura, contorno -> G-code
flatdeck/model.py       progetto: livelli, trasformazioni, origine, contorno della scheda (senza Qt)
flatdeck/viewer.py      vista 2D (QPainter): pan, zoom, griglia in mm dall'origine
flatdeck/mainwindow.py  finestra principale      flatdeck/splash.py  schermata iniziale
```

## Sviluppo

```bash
pip install -e .[dev]
python -m pytest                                              # parser, CAM, modello, UI offscreen
python examples/make_demo.py                                  # rigenera il rame/contorno di esempio
QT_QPA_PLATFORM=offscreen python3 docs/make_screenshots.py    # rigenera le immagini del README
```

Decisioni da non cambiare senza motivo: la geometria è float in mm, Clipper lavora a 0,1 µm; dopo ogni specchiatura si rifà l'unione
(altrimenti l'orientamento dei contorni si inverte e gli offset vanno al contrario); il tratto di una linea con apertura rotonda è un
`ET_OPENROUND`, non un rettangolo; `bounds` del progetto ignora il livello contorno finché c'è altro, così l'origine segue il rame.
