"""A datasheet the catalogue has no category for must say so.

Wurth's 140356145100 is a VDE/UL-approved optocoupler. Asked for as a mosfet
(a refdes prefix is a weak hint and this one was wrong), the librarian read its
datasheet, found no drain-source voltage, no drain current and no on-resistance,
and reported "the datasheet reading is missing drainSourceVoltage,
continuousDrainCurrent, onResistance" — which describes the question, not the
part, and sends someone to check a datasheet that is perfectly good.
"""

from __future__ import annotations

from heaviside.librarian.fetcher.from_datasheet import (
    _missing_why,
    foreign_device_class,
)

_OPTO = """WL-OCPT Optocoupler Phototransistor Output
VDE Approval 40051484 [DIN EN 60747-5-5 (VDE0884-5); EN 60747-5-5:2011]
cULus Approval E513104 [UL 1577]
Anode  Collector
Cathode  Emitter
Current Transfer Ratio 100 %
"""

# A real datasheet's first page: title, summary table, features and
# applications, and only then a line about what the part is used with. The
# mention sits where it does on the page it was copied from — past the title,
# inside the title BLOCK — which is the case the two marker tiers exist for.
_MOSFET = """Si4850EY
N-Channel 60 V (D-S) MOSFET
Vishay Siliconix
PRODUCT SUMMARY
VDS (V) 60   RDS(on) (Ohm) 0.022 at VGS = 10 V   ID (A) 8.5
FEATURES
  TrenchFET power MOSFET
  100 % Rg and UIS tested
  Compliant to RoHS Directive 2002/95/EC
  Halogen-free according to IEC 61249-2-21 definition
  Material categorization: for definitions of compliance
  please see www.vishay.com/doc?99912
DESCRIPTION
  This N-channel enhancement mode power MOSFET is produced
  using Vishay high voltage process technology. This advanced
  technology has been tailored to minimise on-state resistance,
  provide superior switching performance and withstand a high
  energy pulse in avalanche and commutation mode.
APPLICATIONS
  Primary-side switch in an isolated flyback, where the feedback
  loop drives an optocoupler on the secondary side.
"""


def test_an_optocoupler_datasheet_is_recognised_as_one():
    assert "optocoupler" in foreign_device_class(_OPTO)


def test_a_mosfet_that_merely_mentions_an_optocoupler_is_still_a_mosfet():
    # read from the title block only, for the same reason technology_from_text
    # is: an application note is not a change of device class
    assert foreign_device_class(_MOSFET) == ""


def test_an_ordinary_datasheet_names_no_foreign_class():
    assert foreign_device_class("Aluminium Electrolytic Capacitor 100 uF 25 V") == ""


def test_one_missing_field_reads_as_an_incomplete_reading():
    why = _missing_why("mosfet", ["onResistance"],
                       ("drainSourceVoltage", "continuousDrainCurrent", "onResistance"))
    assert "missing onResistance" in why
    assert "not a mosfet's datasheet" not in why


def test_every_field_missing_says_the_document_is_probably_another_kind_of_part():
    req = ("drainSourceVoltage", "continuousDrainCurrent", "onResistance")
    why = _missing_why("mosfet", list(req), req)
    assert "every field a mosfet record requires" in why
    assert "not a mosfet's datasheet" in why


def test_the_dropped_trail_survives_the_stronger_message():
    req = ("drainSourceVoltage", "continuousDrainCurrent", "onResistance")
    why = _missing_why("mosfet", list(req), req,
                       ["onResistance: two readings of the datasheet disagreed"])
    assert "not a mosfet's datasheet" in why
    assert "two readings of the datasheet disagreed" in why
