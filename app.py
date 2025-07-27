import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo  # Python ≥ 3.9
import matplotlib.pyplot as plt
import bcrypt

# ---------- Konstante Pfade ----------
PLAYERS = Path("players.csv")
MATCHES = Path("matches.csv")
PENDING = Path("pending_matches.csv")

# ---------- Hilfsfunktionen ----------
def load_csv(path, cols):
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame(columns=cols)

def save_csv(df, path):
    df.to_csv(path, index=False)

def load_or_create(path: Path, cols: list[str]) -> pd.DataFrame:
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame(columns=cols)

def calc_elo(r_a, r_b, score_a, k=32):
    """Klassische ELO-Formel (1 = Sieg, 0 = Niederlage)"""
    exp_a = 1 / (1 + 10 ** ((r_b - r_a) / 400))
    return round(r_a + k * (score_a - exp_a), 0)

# ---------- PIN-Hashing ----------
def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()

def check_pin(pin: str, stored: str) -> bool:
    """
    Vergleicht die eingegebene PIN mit dem gespeicherten Wert.
    Unterstützt sowohl Klartext (Legacy) als auch bcrypt-Hashes.
    """
    if stored.startswith("$2b$") or stored.startswith("$2a$"):
        # bcrypt-Hash
        return bcrypt.checkpw(pin.encode(), stored.encode())
    else:
        # Legacy: Klartext
        return pin == stored

# ---------- Spieler-Stats & ELO komplett neu berechnen ----------
def rebuild_players(players_df: pd.DataFrame, matches_df: pd.DataFrame, k: int = 32) -> pd.DataFrame:
    """
    Setzt alle Spieler-Statistiken zurück und berechnet sie anhand der
    chronologisch sortierten Match-Liste neu.
    """
    players_df = players_df.copy()
    # Basiswerte zurücksetzen
    players_df[["ELO", "Siege", "Niederlagen", "Spiele"]] = 0
    players_df["ELO"] = 1200

    if matches_df.empty:
        return players_df

    # Matches nach Datum aufsteigend sortieren
    matches_sorted = matches_df.sort_values("Datum")

    for _, row in matches_sorted.iterrows():
        a, b = row["A"], row["B"]
        pa, pb = int(row["PunkteA"]), int(row["PunkteB"])

        # Falls Spieler inzwischen gelöscht wurden, Match überspringen
        if a not in players_df["Name"].values or b not in players_df["Name"].values:
            continue

        r_a = players_df.loc[players_df["Name"] == a, "ELO"].iat[0]
        r_b = players_df.loc[players_df["Name"] == b, "ELO"].iat[0]

        score_a = 1 if pa > pb else 0
        score_b = 1 - score_a

        new_r_a = calc_elo(r_a, r_b, score_a, k)
        new_r_b = calc_elo(r_b, r_a, score_b, k)

        players_df.loc[players_df["Name"] == a, ["ELO", "Siege", "Niederlagen", "Spiele"]] = [
            new_r_a,
            players_df.loc[players_df["Name"] == a, "Siege"].iat[0] + score_a,
            players_df.loc[players_df["Name"] == a, "Niederlagen"].iat[0] + score_b,
            players_df.loc[players_df["Name"] == a, "Spiele"].iat[0] + 1,
        ]
        players_df.loc[players_df["Name"] == b, ["ELO", "Siege", "Niederlagen", "Spiele"]] = [
            new_r_b,
            players_df.loc[players_df["Name"] == b, "Siege"].iat[0] + score_b,
            players_df.loc[players_df["Name"] == b, "Niederlagen"].iat[0] + score_a,
            players_df.loc[players_df["Name"] == b, "Spiele"].iat[0] + 1,
        ]
    return players_df

# ---------- ELO‑Verlauf für einen Spieler ----------
def elo_history(player: str, matches_df: pd.DataFrame, k: int = 32) -> list[tuple[pd.Timestamp, int]]:
    """
    Gibt eine Liste (Datum, Elo) für den angegebenen Spieler zurück.
    Die Berechnung läuft chronologisch über alle Matches.
    """
    ratings: dict[str, int] = {}
    history: list[tuple[pd.Timestamp, int]] = []
    matches_sorted = matches_df.sort_values("Datum")
    for _, row in matches_sorted.iterrows():
        a, b = row["A"], row["B"]
        pa, pb = int(row["PunkteA"]), int(row["PunkteB"])

        # Ratings initialisieren, falls Spieler noch nicht bekannt
        ratings.setdefault(a, 1200)
        ratings.setdefault(b, 1200)

        ra, rb = ratings[a], ratings[b]
        score_a = 1 if pa > pb else 0
        score_b = 1 - score_a

        new_ra = calc_elo(ra, rb, score_a, k)
        new_rb = calc_elo(rb, ra, score_b, k)

        ratings[a] = new_ra
        ratings[b] = new_rb

        if a == player:
            history.append((row["Datum"], new_ra))
        elif b == player:
            history.append((row["Datum"], new_rb))

    return history

# ---------- Daten laden ----------
players = load_or_create(PLAYERS, ["Name", "ELO", "Siege", "Niederlagen", "Spiele", "Pin"])
# Falls alte CSV noch keine Pin‑Spalte hatte
if "Pin" not in players.columns:
    players["Pin"] = ""

matches = load_or_create(MATCHES, ["Datum", "A", "B", "PunkteA", "PunkteB"])
pending = load_or_create(PENDING, ["Datum", "A", "B", "PunkteA", "PunkteB", "confA", "confB"])
for df in (matches, pending):
    if not df.empty:
        df["Datum"] = (
            pd.to_datetime(df["Datum"], utc=True, errors="coerce")
              .dt.tz_convert("Europe/Berlin")
        )


# ---------- Login / Registrierung ----------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "current_player" not in st.session_state:
    st.session_state.current_player = None

if not st.session_state.logged_in:
    with st.sidebar:  # Login‑UI nur solange nicht eingeloggt
        st.header("Login / Registrieren")
        default_mode = "Registrieren" if players.empty else "Login"
        mode = st.radio("Aktion wählen", ("Login", "Registrieren"),
                        index=0 if default_mode == "Login" else 1)

        if mode == "Login":
            if players.empty:
                st.info("Noch keine Spieler angelegt.")
            else:
                login_name = st.selectbox("Spieler", players["Name"])
                login_pin = st.text_input("PIN", type="password")
                if st.button("Einloggen"):
                    stored_pin = players.loc[players["Name"] == login_name, "Pin"].iat[0]
                    if check_pin(login_pin, stored_pin):
                        # Falls PIN noch im Klartext war: sofort hash speichern
                        if not stored_pin.startswith("$2b$") and not stored_pin.startswith("$2a$"):
                            players.loc[players["Name"] == login_name, "Pin"] = hash_pin(login_pin)
                            save_csv(players, PLAYERS)
                        st.session_state.logged_in = True
                        st.session_state.current_player = login_name
                        st.rerun()
                    else:
                        st.error("Falsche PIN")

        elif mode == "Registrieren":
            reg_name = st.text_input("Neuer Spielername")
            reg_pin1 = st.text_input("PIN wählen (4-stellig)", type="password")
            reg_pin2 = st.text_input("PIN bestätigen", type="password")
            if st.button("Registrieren"):
                if reg_name == "" or reg_pin1 == "":
                    st.warning("Name und PIN eingeben.")
                elif reg_pin1 != reg_pin2:
                    st.warning("PINs stimmen nicht überein.")
                elif reg_name in players["Name"].values:
                    st.warning("Spieler existiert bereits.")
                else:
                    players.loc[len(players)] = [reg_name, 1200, 0, 0, 0, hash_pin(reg_pin1)]
                    save_csv(players, PLAYERS)
                    st.success(f"{reg_name} angelegt. Jetzt einloggen.")
                    st.rerun()
else:
    # Eingeloggt: Sidebar zeigt nur Logout und aktuellen User
    with st.sidebar:
        st.markdown(f"**Eingeloggt als:** {st.session_state.current_player}")
        if st.button("Logout"):
            st.session_state.logged_in = False
            st.session_state.current_player = None
            st.rerun()
# Login erforderlich, um fortzufahren
if not st.session_state.logged_in:
    st.stop()

current_player = st.session_state.current_player



# ---------- Match erfassen ----------
st.title("AK-Tischtennis")
st.subheader("Match eintragen")

if len(players) < 2:
    st.info("Mindestens zwei Spieler registrieren, um ein Match anzulegen.")
else:
    st.markdown(f"**Eingeloggt als:** {current_player}")
    pa = st.number_input("Punkte (dein Ergebnis)", 0, 21, 11)
    b = st.selectbox("Gegner wählen", players[players["Name"] != current_player]["Name"])
    pb = st.number_input("Punkte Gegner", 0, 21, 8)
    if st.button("Match speichern"):
        if current_player == b:
            st.error("Spieler dürfen nicht identisch sein.")
        else:
            ts_now = datetime.now(ZoneInfo("Europe/Berlin"))
            pending.loc[len(pending)] = [
                ts_now, current_player, b, pa, pb,
                True,  # confA (du)
                False  # confB
            ]
            save_csv(pending, PENDING)
            st.success("Match gespeichert! Es wartet jetzt auf Bestätigung des Gegners.")


# ---------- Offene Matches bestätigen ----------
with st.expander("Offene Matches bestätigen"):
    to_confirm = pending[
        ((pending["A"] == current_player) & (pending["confA"] == False)) |
        ((pending["B"] == current_player) & (pending["confB"] == False))
    ]
    if to_confirm.empty:
        st.info("Keine offenen Matches für dich.")
    else:
        for idx, row in to_confirm.iterrows():
            match_text = (f"{row['A']} {row['PunkteA']} : {row['PunkteB']} {row['B']}  "
                          f"({row['Datum'].strftime('%d.%m.%Y %H:%M')})")
            st.write(match_text)

            col_ok, col_rej = st.columns(2)
            with col_ok:
                if st.button("Bestätigen", key=f"conf_{idx}"):
                    if row["A"] == current_player:
                        pending.at[idx, "confA"] = True
                    else:
                        pending.at[idx, "confB"] = True
                    # Wenn beide bestätigt, Match finalisieren
                    if pending.at[idx, "confA"] and pending.at[idx, "confB"]:
                        matches = pd.concat(
                            [matches,
                             pending.loc[[idx], ["Datum","A","B","PunkteA","PunkteB"]]],
                            ignore_index=True
                        )
                        pending.drop(idx, inplace=True)
                        players = rebuild_players(players, matches)
                        save_csv(matches, MATCHES)
                    save_csv(pending, PENDING)
                    save_csv(players, PLAYERS)
                    st.rerun()

            with col_rej:
                if st.button("Ablehnen", key=f"rej_{idx}"):
                    # Einfach aus der Pending-Liste entfernen, keine ELO-Anpassung
                    pending.drop(idx, inplace=True)
                    save_csv(pending, PENDING)
                    st.success("Match abgelehnt und entfernt.")
                    st.rerun()


# ---------- Leaderboard anzeigen ----------

# ---------- Leaderboard anzeigen ----------
st.subheader("Leaderboard")
st.dataframe(
    players.drop(columns=["Pin"], errors="ignore")
           .sort_values("ELO", ascending=False)
           .reset_index(drop=True)
)

# ---------- ELO‑Verlauf Plot ----------
with st.expander("Mein ELO‑Verlauf", expanded=True):
    player_matches = matches[(matches["A"] == current_player) | (matches["B"] == current_player)]
    if player_matches.empty:
        st.info("Für dich existieren noch keine bestätigten Matches.")
    else:
        hist = elo_history(current_player, matches)
        elos = [e for _, e in hist]
        x_vals = list(range(1, len(elos) + 1))  # 1, 2, 3, ...
        plt.figure()
        plt.plot(x_vals, elos, marker="o")
        plt.xlabel("Match‑Nr.")
        plt.ylabel("ELO")
        plt.title(f"ELO‑Verlauf von {current_player}")
        plt.xticks(x_vals)  # jede Match‑Nr. anzeigen
        st.pyplot(plt)

# ---------- Letzte 5 Matches ----------
st.subheader("Letzte 5 Matches")
if matches.empty:
    st.info("Noch keine Spiele eingetragen.")
else:
    recent = (
        matches.sort_values("Datum", ascending=False)
        .head(5)
        .reset_index(drop=True)
    )
    recent_display = recent.copy()
    recent_display["Datum"] = recent_display["Datum"].dt.strftime("%d.%m.%Y")
    st.dataframe(recent_display)
