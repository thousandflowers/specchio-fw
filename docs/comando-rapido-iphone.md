# La sveglia dello specchio segue le sveglie dell'iPhone

Si fa una volta sola, in circa 5 minuti, dall'app **Comandi Rapidi** dell'iPhone.
Dopo, lo specchio conosce da solo la prossima sveglia attiva: alba di luce 30 minuti prima.

I nomi delle azioni possono cambiare leggermente tra versioni di iOS: tra parentesi c'è il nome inglese.
L'iPhone deve essere sul Wi-Fi di casa. Al posto di `INDIRIZZO` metti l'indirizzo della tua striscia sulla rete di casa (lo vedi nel router, o usa `specchio.local`).

## 1. Il comando "Sveglia specchio"

1. Comandi Rapidi → **+** → rinomina in `Sveglia specchio`.
2. Aggiungi l'azione **Trova sveglie** (Find Alarms). Tocca "Aggiungi filtro" e scegli **È attiva** (Is Enabled) **è sì**.
3. Aggiungi l'azione **Testo** (Text). Dentro il testo inserisci la variabile **Sveglie**, poi tocca la variabile e scegli la proprietà **Ora** (Time).
4. Aggiungi l'azione **Ottieni contenuti di URL** (Get Contents of URL):
   - URL: `http://INDIRIZZO/api/wake`
   - Metodo: **POST**
   - Corpo della richiesta: **File**, e come file scegli il **Testo** del passo 3.
5. Premi ▶ per provarlo. Poi apri `http://INDIRIZZO` in Safari: nel campo "Sveglia" deve comparire l'orario della tua prossima sveglia.

La striscia accetta qualsiasi testo che contenga orari (`07:30`, `7.30`, `7:30 PM`, una data con l'ora) e tiene il più vicino.
Se non ci sono sveglie attive, il testo è vuoto e la striscia cancella la sua.

## 2. Farlo partire da solo

Comandi Rapidi → **Automazione** → **+**. Per ognuna scegli "Esegui subito" (Run Immediately), senza chiedere conferma.

1. **App** → Orologio → **Viene chiusa** → esegui `Sveglia specchio`.
   Ogni volta che cambi una sveglia ed esci dall'Orologio, lo specchio si aggiorna.
2. **Ora del giorno** → 22:30, ogni giorno → esegui `Sveglia specchio`.
   È la rete di sicurezza: copre le sveglie che si ripetono.
3. **Sveglia** → **Viene interrotta** (Is Stopped), qualsiasi sveglia → azione **Ottieni contenuti di URL** con `http://INDIRIZZO/api/wake?stop=1`.
   Quando fermi la sveglia sul telefono, lo specchio smette di insistere e resta acceso.

## Limiti di iOS

- **Sveglia del programma Sonno:** non compare tra le sveglie. Usa una sveglia normale dell'Orologio, oppure aggiungi un'automazione "Sonno → Sveglia in corso" che chiama `http://INDIRIZZO/api/wake?ring=1` (alba immediata di un minuto).
- **App di terze parti** (Sleep Cycle e simili): non dicono a iOS a che ora suoneranno. Soluzione pratica: tieni nell'Orologio una sveglia alla stessa ora, anche senza suono, e sarà quella a guidare la luce.
- Fuori casa il comando non raggiunge la striscia: resta valida l'ultima sveglia ricevuta.

## Senza Comandi Rapidi

Apri `http://INDIRIZZO` dal telefono e scrivi l'orario nel campo "Sveglia". Vale fino alla prossima modifica.
