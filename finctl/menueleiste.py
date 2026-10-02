"""Ein Symbol in der Menueleiste des Mac: oeffnen und beenden.

Die App hat kein Fenster und kein Dock-Symbol (LSUIElement im Bauplan). Ohne
das Symbol oben rechts sah man ihr nicht an, ob sie laeuft, und kam nach dem
Schliessen des Tabs nur ueber einen zweiten Doppelklick zurueck.

Das Bild ist ein SF Symbol des Systems: einfarbig, als Vorlage gezeichnet,
damit macOS es fuer helle und dunkle Leisten selbst einfaerbt -- wie die
Symbole des Systems daneben. Ein farbiges App-Symbol faellt dort heraus.

AppKit will den Hauptfaden; der Server laeuft deshalb in einem zweiten.
Ohne AppKit (Windows, ein Python ohne pyobjc) bleibt alles wie vorher: der
Server im Vordergrund, beendet ueber den Knopf in der App.
"""

from __future__ import annotations

import functools
import signal
import sys
import threading
import webbrowser

#: SF Symbol: ein Euro im Kreis -- Geld, ohne eine Bank zu zeigen.
SYMBOL = "eurosign.circle"
#: Steht in der Leiste, falls das System das Symbol nicht kennt.
ERSATZ = "€"


def verfuegbar() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import AppKit  # noqa: F401
        from PyObjCTools import AppHelper  # noqa: F401
    except ImportError:
        return False
    return True


@functools.cache
def _ziel():
    """Die Klasse, an die das Menue meldet -- einmal je Prozess.

    Eine Objective-C-Klasse laesst sich nicht zweimal unter demselben Namen
    anlegen; deshalb hier und nicht im Aufruf.
    """
    from Foundation import NSObject

    class FinanceOSMenueleiste(NSObject):
        def oeffnen_(self, _absender) -> None:
            webbrowser.open(self.adresse)

        def beenden_(self, _absender) -> None:
            self.server.should_exit = True

    return FinanceOSMenueleiste


def _menue(ziel, name: str):
    import AppKit

    menue = AppKit.NSMenu.alloc().init()
    for titel, aktion in ((f"{name} öffnen", "oeffnen:"), (None, None), ("Beenden", "beenden:")):
        if titel is None:
            menue.addItem_(AppKit.NSMenuItem.separatorItem())
            continue
        punkt = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(titel, aktion, "")
        punkt.setTarget_(ziel)
        menue.addItem_(punkt)
    return menue


def _leeres_ereignis():
    import AppKit

    return AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(  # noqa: E501
        AppKit.NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0)


def laufen(server, adresse: str, name: str = "Finance OS") -> None:
    """Server im Hintergrund, Symbol im Vordergrund. Endet mit dem Server.

    "Beenden" im Menue und der Knopf in der App tun dasselbe: der Server hoert
    auf, und mit ihm die Ereignisschleife.
    """
    import AppKit
    from PyObjCTools import AppHelper, MachSignals

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    ziel = _ziel().alloc().init()
    ziel.adresse, ziel.server = adresse, server

    eintrag = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
        AppKit.NSVariableStatusItemLength)
    knopf = eintrag.button()
    bild = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(SYMBOL, name)
    if bild is not None:
        bild.setTemplate_(True)
        knopf.setImage_(bild)
    else:
        knopf.setTitle_(ERSATZ)
    knopf.setToolTip_(name)
    eintrag.setMenu_(_menue(ziel, name))

    def anhalten(*_) -> None:
        # Nicht AppHelper.stopEventLoop: das ruft NSApp.terminate_, und das
        # beendet den Prozess aus C heraus -- ohne `finally`, ohne dass Python
        # seine Ausgabe schreibt. stop_ laesst run() zurueckkehren, aber erst
        # beim naechsten Ereignis; deshalb eins hinterher.
        app.stop_(None)
        app.postEvent_atStart_(_leeres_ereignis(), True)

    def dienen() -> None:
        try:
            server.run()
        finally:
            AppHelper.callAfter(anhalten)

    # Strg+C im Terminal: ein Python-Signalhandler kaeme in der Schleife von
    # AppKit nie an die Reihe, ein Mach-Signal schon.
    MachSignals.signal(signal.SIGINT, anhalten)
    faden = threading.Thread(target=dienen, daemon=True)
    faden.start()
    try:
        app.run()
    finally:
        # Strg+C im Terminal beendet die Schleife zuerst: dann den Server auch.
        server.should_exit = True
        faden.join(timeout=5)
        AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(eintrag)
