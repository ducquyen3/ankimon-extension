"""Ankimon HUD Extras

Standalone add-on: shows each Pokemon's type(s), CP and BP in a small pill
above its name bar in Ankimon's reviewer HUD.

It does NOT modify any Ankimon file. It wraps Ankimon's
Reviewer_Manager.update_life_bar at runtime and injects the extra HTML/CSS
through the HUD's JS update() function, so Ankimon updates keep it working
(as long as Ankimon keeps those internal names).
"""

from html import escape as _esc
import importlib
import json
import sys

from aqt import gui_hooks, mw
from aqt.qt import QColor, QGraphicsDropShadowEffect, QTimer, Qt

TYPE_COLORS = {
    "Normal": "#A8A77A",
    "Fire": "#EE8130",
    "Water": "#6390F0",
    "Electric": "#F7D02C",
    "Grass": "#7AC74C",
    "Ice": "#96D9D6",
    "Fighting": "#C22E28",
    "Poison": "#A33EA1",
    "Ground": "#E2BF65",
    "Flying": "#A98FF3",
    "Psychic": "#F95587",
    "Bug": "#A6B91A",
    "Rock": "#B6A136",
    "Ghost": "#735797",
    "Dragon": "#6F35FC",
    "Dark": "#705746",
    "Steel": "#B7B7CE",
    "Fairy": "#D685AD",
}

# Gen 6+ type chart: attacker -> {defender: multiplier}; missing = x1.
TYPE_CHART = {
    "Normal": {"Rock": 0.5, "Ghost": 0, "Steel": 0.5},
    "Fire": {"Fire": 0.5, "Water": 0.5, "Grass": 2, "Ice": 2, "Bug": 2, "Rock": 0.5, "Dragon": 0.5, "Steel": 2},
    "Water": {"Fire": 2, "Water": 0.5, "Grass": 0.5, "Ground": 2, "Rock": 2, "Dragon": 0.5},
    "Electric": {"Water": 2, "Electric": 0.5, "Grass": 0.5, "Ground": 0, "Flying": 2, "Dragon": 0.5},
    "Grass": {"Fire": 0.5, "Water": 2, "Grass": 0.5, "Poison": 0.5, "Ground": 2, "Flying": 0.5, "Bug": 0.5, "Rock": 2, "Dragon": 0.5, "Steel": 0.5},
    "Ice": {"Fire": 0.5, "Water": 0.5, "Grass": 2, "Ice": 0.5, "Ground": 2, "Flying": 2, "Dragon": 2, "Steel": 0.5},
    "Fighting": {"Normal": 2, "Ice": 2, "Poison": 0.5, "Flying": 0.5, "Psychic": 0.5, "Bug": 0.5, "Rock": 2, "Ghost": 0, "Dark": 2, "Steel": 2, "Fairy": 0.5},
    "Poison": {"Grass": 2, "Poison": 0.5, "Ground": 0.5, "Rock": 0.5, "Ghost": 0.5, "Steel": 0, "Fairy": 2},
    "Ground": {"Fire": 2, "Electric": 2, "Grass": 0.5, "Poison": 2, "Flying": 0, "Bug": 0.5, "Rock": 2, "Steel": 2},
    "Flying": {"Electric": 0.5, "Grass": 2, "Fighting": 2, "Bug": 2, "Rock": 0.5, "Steel": 0.5},
    "Psychic": {"Fighting": 2, "Poison": 2, "Psychic": 0.5, "Dark": 0, "Steel": 0.5},
    "Bug": {"Fire": 0.5, "Grass": 2, "Fighting": 0.5, "Poison": 0.5, "Flying": 0.5, "Psychic": 2, "Ghost": 0.5, "Dark": 2, "Steel": 0.5, "Fairy": 0.5},
    "Rock": {"Fire": 2, "Ice": 2, "Fighting": 0.5, "Ground": 0.5, "Flying": 2, "Bug": 2, "Steel": 0.5},
    "Ghost": {"Normal": 0, "Psychic": 2, "Ghost": 2, "Dark": 0.5},
    "Dragon": {"Dragon": 2, "Steel": 0.5, "Fairy": 0},
    "Dark": {"Fighting": 0.5, "Psychic": 2, "Ghost": 2, "Dark": 0.5, "Fairy": 0.5},
    "Steel": {"Fire": 0.5, "Water": 0.5, "Electric": 0.5, "Ice": 2, "Rock": 2, "Steel": 0.5, "Fairy": 2},
    "Fairy": {"Fire": 0.5, "Fighting": 2, "Poison": 0.5, "Dragon": 2, "Dark": 2, "Steel": 0.5},
}

# Latest battle types, refreshed on every HUD repaint; the move dialog reads it.
_STATE = {"enemy": [], "main": []}


def _norm_types(raw):
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    return [str(t).strip().capitalize() for t in raw if t]


def _mult(attack_type, defender_types):
    """Damage multiplier of one attacking type against 1-2 defending types."""
    m = 1.0
    row = TYPE_CHART.get(attack_type, {})
    for d in defender_types:
        m *= row.get(d, 1)
    return m


def _glow(label, mult):
    """Mark a move row: green border = good for the player, red = bad, none = x1.

    A border instead of a drop-shadow effect: the shadow bled into the row's
    transparent background and tinted the whole row.
    """
    label.setGraphicsEffect(None)
    label._hudx_color = None if mult == 1 else ("#39ff14" if mult > 1 else "#ff2a2a")


_started = False
_timer = None


def _find_manager_class():
    """Locate Ankimon's Reviewer_Manager class whatever the add-on folder is called."""
    for name, mod in list(sys.modules.items()):
        if name.endswith(".pyobj.reviewer_obj") or name == "pyobj.reviewer_obj":
            cls = getattr(mod, "Reviewer_Manager", None)
            if cls is not None:
                return cls
    return None


def _business_module(mgr):
    root = type(mgr).__module__.rsplit(".pyobj.", 1)[0]
    return importlib.import_module(root + ".business")


def _info_html(biz, pkmn, opponent, element_id, own=True):
    badges = ""
    raw_types = getattr(pkmn, "type", None) or []
    if isinstance(raw_types, str):
        raw_types = [raw_types]
    for t in raw_types:
        name = str(t).capitalize()
        color = TYPE_COLORS.get(name, "#777777")
        opp_types = _norm_types(getattr(opponent, "type", None))
        # Same rule for BOTH sides, judged type-vs-type from the badge owner's
        # point of view (each opponent type checked on its own):
        # green = this type is super effective against an opponent type (khac),
        # red   = an opponent type is super effective against this type (bi khac),
        # split = both.
        good = any(_mult(name, [o]) > 1 for o in opp_types)
        bad = any(_mult(o, [name]) > 1 for o in opp_types)
        if good and bad:
            cls = " hudx-both"
        elif good:
            cls = " hudx-good"
        elif bad:
            cls = " hudx-bad"
        else:
            cls = ""
        badges += (
            f'<span class="hudx-type{cls}" style="background:{color}">{name}</span>'
        )

    stats = ""
    try:
        cp = int(pkmn.cp)
        stages = getattr(pkmn, "stat_stages", None) or {}
        compat = biz.type_compatibility_multiplier(
            getattr(pkmn, "type", None), getattr(opponent, "type", None)
        )
        bp = biz.calculate_present_power(
            cp,
            pkmn.hp,
            pkmn.max_hp,
            compat,
            stages.get("atk", 0),
            stages.get("spa", 0),
        )
        fmt = biz.format_compact_number
        stats = f"<span>CP: {fmt(cp)} BP: {fmt(bp)}</span>"
    except Exception:
        pass

    if not (badges or stats):
        return ""
    return f'<div id="{element_id}" class="Ankimon hudx-pill">{badges}{stats}</div>'


def _css(settings, mode):
    xp_bar_spacer = int(settings.compute_special_variable("xp_bar_spacer"))
    hp_bar_thickness = int(settings.get("gui.review_hp_bar_thickness")) * 4

    if mode == 2:
        my_bottom = 58 + xp_bar_spacer + hp_bar_thickness
        enemy_bottom = 53 + xp_bar_spacer
        enemy_right = "140px"
    else:
        my_bottom = 40 + hp_bar_thickness + xp_bar_spacer
        enemy_bottom = 40 + hp_bar_thickness + xp_bar_spacer
        # Keep clear of the BATTLING badge, which sits at right: 15%.
        enemy_right = "calc(15% + 120px)"

    return (
        "#ankimon-hud #hudx-my { position: fixed; bottom: %dpx; left: 125px; }\n"
        "#ankimon-hud #hudx-enemy { position: fixed; bottom: %dpx; right: %s; }\n"
        "#ankimon-hud .hudx-pill {"
        " z-index: 9999; display: flex; align-items: center; gap: 6px;"
        " white-space: nowrap; color: #FFFFFF; font-family: Arial, sans-serif;"
        " font-size: 14px; font-weight: bold;"
        " background-color: rgba(0, 0, 0, 0.85);"
        " box-shadow: none !important; filter: none !important;"
        " outline: 1.5px solid var(--ankimon-outline, #FFFFFF);"
        " border-radius: 5px; padding: 3px 8px; }\n"
        "#ankimon-hud .hudx-type {"
        " color: #FFFFFF; font-size: 12px; font-weight: bold;"
        " text-transform: uppercase;"
        " text-shadow: 0 0 2px #000, 0 0 2px #000;"
        " border-radius: 4px; padding: 1px 7px; }\n"
        "#ankimon-hud .hudx-good { box-shadow: 0 0 6px 2px #39ff14, 0 0 14px 5px rgba(57, 255, 20, 0.55); }\n"
        "#ankimon-hud .hudx-bad { box-shadow: 0 0 6px 2px #ff2a2a, 0 0 14px 5px rgba(255, 42, 42, 0.55); }\n"
        "#ankimon-hud .hudx-both { box-shadow: -4px 0 8px 2px #39ff14, 4px 0 8px 2px #ff2a2a; }\n"
    ) % (my_bottom, enemy_bottom, enemy_right)


# Runs in the reviewer webview: patch the HUD's update() once so every repaint
# also carries our extra pills (inserted just before the HUD root's closing tag).
_JS_TEMPLATE = """
(function(extra){
  window.__hudxExtra = extra;
  var hud = window.__ankimonHud;
  if (hud && !hud.__hudxPatched) {
    var orig = hud.update;
    hud.update = function(html, css) {
      try {
        var ex = window.__hudxExtra;
        if (ex && ex.html && (html || '').indexOf('id="hudx-') < 0) {
          var i = (html || '').lastIndexOf('</div>');
          if (i >= 0) { html = html.slice(0, i) + ex.html + html.slice(i); }
          css = (css || '') + (ex.css || '');
        }
      } catch (e) {}
      return orig.call(hud, html, css);
    };
    hud.__hudxPatched = true;
  }
})(%s);
"""


def _prepare(mgr, reviewer, card, ease):
    # Same guards as Ankimon: only direct repaint calls (card=0, ease=0).
    if isinstance(ease, int) and ease != 0:
        return
    if card is not None and not isinstance(card, int):
        return

    enemy = mgr.enemy_pokemon
    main = mgr.main_pokemon
    if enemy is None or main is None:
        return

    _STATE["enemy"] = _norm_types(getattr(enemy, "type", None))
    _STATE["main"] = _norm_types(getattr(main, "type", None))

    settings = mgr.settings
    mode = int(settings.get("gui.show_mainpkmn_in_reviewer"))
    if mode == 3:
        return

    biz = _business_module(mgr)
    html = _info_html(biz, enemy, main, "hudx-enemy", own=False)
    if mode > 0:
        html += _info_html(biz, main, enemy, "hudx-my")
    css = _css(settings, mode)

    # If our extras changed (e.g. level-up), force Ankimon to repaint once.
    sig = (html, css)
    if getattr(mgr, "_hudx_sig", None) != sig:
        mgr._hudx_sig = sig
        mgr._last_state = None

    payload = json.dumps({"html": html, "css": css})
    reviewer.web.eval(_JS_TEMPLATE % payload)


def _wrap(orig):
    def wrapper(self, reviewer, card, ease, *args, **kwargs):
        try:
            _prepare(self, reviewer, card, ease)
        except Exception:
            pass  # never let the extras break Ankimon's own HUD
        return orig(self, reviewer, card, ease, *args, **kwargs)

    wrapper._hudx_wrapped = True
    wrapper._hudx_orig = orig
    return wrapper


def _try_patch():
    try:
        cls = _find_manager_class()
        if cls is None:
            return
        current = cls.update_life_bar
        if getattr(current, "_hudx_wrapped", False):
            return
        cls.update_life_bar = _wrap(current)
    except Exception:
        pass


def _find_dialog_class():
    """Locate Ankimon's MoveSelectionDialog (the 'Select a Move' window)."""
    for name, mod in list(sys.modules.items()):
        if name.endswith(".classes.choose_move_dialog") or name == "classes.choose_move_dialog":
            cls = getattr(mod, "MoveSelectionDialog", None)
            if cls is not None:
                return cls
    return None


def _decorate_dialog(dlg):
    """Rewrite each move row so it shows the move's type as a coloured tag."""
    root = type(dlg).__module__.rsplit(".classes.", 1)[0]
    pdx = importlib.import_module(root + ".functions.pokedex_functions")
    names = importlib.import_module(root + ".move_names")

    for index, (move, label) in enumerate(zip(dlg.mainpokemon_attacks, dlg.move_labels)):
        detail = pdx.find_details_move(move) or {}
        move_type = str(detail.get("type") or "").strip().capitalize()
        if not move_type:
            continue
        color = TYPE_COLORS.get(move_type, "#777777")
        name = names.format_move_name(detail.get("name", move))
        power = detail.get("basePower", "Unknown")
        desc = detail.get("shortDesc", "Unknown")
        category = str(detail.get("category") or "").lower()
        if category != "status" and _STATE.get("enemy"):
            _glow(label, _mult(move_type, _STATE["enemy"]))
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(
            f'{index + 1}. '
            f'<span style="background-color:{color}; color:#FFFFFF;">'
            f'&nbsp;{_esc(move_type.upper())}&nbsp;</span> '
            f'{_esc(str(name))}({_esc(str(power))}): {_esc(str(desc))}'
        )
    dlg.update_highlight()


def _wrap_dialog_init(orig):
    def wrapper(self, *args, **kwargs):
        orig(self, *args, **kwargs)
        try:
            _decorate_dialog(self)
        except Exception:
            pass  # never break the move picker itself

    wrapper._hudx_wrapped = True
    wrapper._hudx_orig = orig
    return wrapper


def _patch_highlight(cls):
    """Re-apply our border colour every time Ankimon restyles the move rows."""
    orig = cls.update_highlight
    if getattr(orig, "_hudx_wrapped", False):
        return

    def update_highlight(self):
        orig(self)
        try:
            for label in self.move_labels:
                color = getattr(label, "_hudx_color", None)
                if color:
                    label.setStyleSheet(
                        label.styleSheet() + f" border: 2px solid {color};"
                    )
        except Exception:
            pass

    update_highlight._hudx_wrapped = True
    cls.update_highlight = update_highlight


def _try_patch_dialog():
    try:
        cls = _find_dialog_class()
        if cls is None:
            return
        current = cls.__init__
        if getattr(current, "_hudx_wrapped", False):
            return
        cls.__init__ = _wrap_dialog_init(current)
        _patch_highlight(cls)
    except Exception:
        pass


def _find_reviewer_ui_module():
    """Locate Ankimon's reviewer_ui module (owner of the team-cycle hotkey)."""
    for name, mod in list(sys.modules.items()):
        if name.endswith(".reviewer_ui") and hasattr(mod, "cycle_team_pokemon"):
            return mod
    return None


def _save_outgoing_hp(ui_mod):
    """Persist the active Pokemon's live HP before Ankimon swaps it out.

    Ankimon heals the main Pokemon in memory (e.g. full HP after fainting) but
    the team-cycle hotkey never saves the outgoing Pokemon, so switching back
    reloads its stale HP from the database. It also prefers ``current_hp`` over
    ``hp`` on load, so both fields are synced to the HP the HUD shows.
    """
    main = getattr(ui_mod, "main_pokemon", None)
    if main is None or not getattr(main, "individual_id", None):
        return
    hp = getattr(main, "hp", None)
    if hp is None:
        return
    try:
        main.current_hp = hp
    except Exception:
        pass
    root = ui_mod.__name__.rsplit(".", 1)[0]
    upd = importlib.import_module(root + ".functions.update_main_pokemon")
    upd.save_main_pokemon(main)


def _wrap_cycle(ui_mod, orig):
    def cycle_team_pokemon(*args, **kwargs):
        try:
            _save_outgoing_hp(ui_mod)
        except Exception:
            pass  # never block the switch itself
        return orig(*args, **kwargs)

    cycle_team_pokemon._hudx_wrapped = True
    cycle_team_pokemon._hudx_orig = orig
    return cycle_team_pokemon


def _try_patch_cycle():
    try:
        mod = _find_reviewer_ui_module()
        if mod is None:
            return
        current = mod.cycle_team_pokemon
        if getattr(current, "_hudx_wrapped", False):
            return
        mod.cycle_team_pokemon = _wrap_cycle(mod, current)
    except Exception:
        pass


def _tick():
    _try_patch()
    _try_patch_dialog()
    _try_patch_cycle()


def _start(*_args, **_kwargs):
    global _started, _timer
    if _started:
        return
    _started = True
    _tick()
    # Ankimon can reload its own modules (new class object); re-check regularly
    # so the wrapper is re-applied if that happens.
    _timer = QTimer(mw)
    _timer.timeout.connect(_tick)
    _timer.start(3000)


gui_hooks.main_window_did_init.append(_start)
gui_hooks.profile_did_open.append(_start)
