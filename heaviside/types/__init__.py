"""heaviside.types — schema classes generated from MAS / PEAS / SAS / CAS / RAS.

The ``_generated`` subpackage is **not** committed. ``make types``
(scripts/gen_types.py, quicktype) produces it from the schema submodules
and regenerates it whenever they change; CI generates it before the mypy
and unit-test steps.

Import the top-level classes from here::

    from heaviside.types import Magnetic

    def harvest_isat(magnetic: Magnetic) -> float: ...

Every class carries ``from_dict`` / ``to_dict``. ``from_dict`` raises
``AssertionError`` on any shape mismatch — never a silent default — so it
doubles as a loud validation gate at boundaries.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

#: Exported class name → generated module path (relative to this package).
#: Names like ``Inputs`` / ``Outputs`` exist once per schema repo and are
#: deliberately not exported here — import those from their full
#: ``heaviside.types._generated.<repo>.<module>`` path.
_EXPORTS: dict[str, str] = {
    # MAS
    "Mas": "_generated.mas.mas",
    "Magnetic": "_generated.mas.magnetic",
    "Core": "_generated.mas.core",
    "Coil": "_generated.mas.coil",
    "Wire": "_generated.mas.wire",
    "Bobbin": "_generated.mas.bobbin",
    # PEAS — the PSMA "peas-family-consolidation" folded Controller into peas.json
    # (it now $refs CTAS) and removed the standalone Terminal type, so both live
    # in the single generated peas module (Terminal no longer exported by PEAS).
    "Peas": "_generated.peas.peas",
    "Controller": "_generated.peas.peas",
    # CAS
    "Cas": "_generated.cas.cas",
    "Capacitor": "_generated.cas.capacitor",
    # RAS
    "Ras": "_generated.ras.ras",
    "Resistor": "_generated.ras.resistor",
    "Varistor": "_generated.ras.varistor",
    # SAS
    "Sas": "_generated.sas.sas",
    "Diode": "_generated.sas.diode",
    "Mosfet": "_generated.sas.mosfet",
    "Igbt": "_generated.sas.igbt",
    "Bjt": "_generated.sas.bjt",
    # MAS converter-topology inputs
    "AsymmetricHalfBridge": "_generated.topologies.asymmetric_half_bridge",
    "Boost": "_generated.topologies.boost",
    "Buck": "_generated.topologies.buck",
    "CllcResonant": "_generated.topologies.cllc_resonant",
    "ClllcResonant": "_generated.topologies.clllc_resonant",
    "CommonModeChoke": "_generated.topologies.common_mode_choke",
    "Cuk": "_generated.topologies.cuk",
    "CurrentTransformer": "_generated.topologies.current_transformer",
    "DifferentialModeChoke": "_generated.topologies.differential_mode_choke",
    "DualActiveBridge": "_generated.topologies.dual_active_bridge",
    "Flyback": "_generated.topologies.flyback",
    "Forward": "_generated.topologies.forward",
    "FourSwitchBuckBoost": "_generated.topologies.four_switch_buck_boost",
    "IsolatedBuck": "_generated.topologies.isolated_buck",
    "IsolatedBuckBoost": "_generated.topologies.isolated_buck_boost",
    "LlcResonant": "_generated.topologies.llc_resonant",
    "PhaseShiftedFullBridge": "_generated.topologies.phase_shifted_full_bridge",
    "PhaseShiftedHalfBridge": "_generated.topologies.phase_shifted_half_bridge",
    "PowerFactorCorrection": "_generated.topologies.power_factor_correction",
    "PushPull": "_generated.topologies.push_pull",
    "Sepic": "_generated.topologies.sepic",
    "SeriesResonant": "_generated.topologies.series_resonant",
    "Vienna": "_generated.topologies.vienna",
    "Weinberg": "_generated.topologies.weinberg",
    "Zeta": "_generated.topologies.zeta",
}

if TYPE_CHECKING:
    from heaviside.types._generated.cas.capacitor import Capacitor as Capacitor
    from heaviside.types._generated.cas.cas import Cas as Cas
    from heaviside.types._generated.mas.bobbin import Bobbin as Bobbin
    from heaviside.types._generated.mas.coil import Coil as Coil
    from heaviside.types._generated.mas.core import Core as Core
    from heaviside.types._generated.mas.magnetic import Magnetic as Magnetic
    from heaviside.types._generated.mas.mas import Mas as Mas
    from heaviside.types._generated.mas.wire import Wire as Wire
    from heaviside.types._generated.peas.peas import Controller as Controller
    from heaviside.types._generated.peas.peas import Peas as Peas
    from heaviside.types._generated.ras.ras import Ras as Ras
    from heaviside.types._generated.ras.resistor import Resistor as Resistor
    from heaviside.types._generated.ras.varistor import Varistor as Varistor
    from heaviside.types._generated.sas.bjt import Bjt as Bjt
    from heaviside.types._generated.sas.diode import Diode as Diode
    from heaviside.types._generated.sas.igbt import Igbt as Igbt
    from heaviside.types._generated.sas.mosfet import Mosfet as Mosfet
    from heaviside.types._generated.sas.sas import Sas as Sas
    from heaviside.types._generated.topologies.asymmetric_half_bridge import (
        AsymmetricHalfBridge as AsymmetricHalfBridge,
    )
    from heaviside.types._generated.topologies.boost import Boost as Boost
    from heaviside.types._generated.topologies.buck import Buck as Buck
    from heaviside.types._generated.topologies.cllc_resonant import (
        CllcResonant as CllcResonant,
    )
    from heaviside.types._generated.topologies.clllc_resonant import (
        ClllcResonant as ClllcResonant,
    )
    from heaviside.types._generated.topologies.common_mode_choke import (
        CommonModeChoke as CommonModeChoke,
    )
    from heaviside.types._generated.topologies.cuk import Cuk as Cuk
    from heaviside.types._generated.topologies.current_transformer import (
        CurrentTransformer as CurrentTransformer,
    )
    from heaviside.types._generated.topologies.differential_mode_choke import (
        DifferentialModeChoke as DifferentialModeChoke,
    )
    from heaviside.types._generated.topologies.dual_active_bridge import (
        DualActiveBridge as DualActiveBridge,
    )
    from heaviside.types._generated.topologies.flyback import Flyback as Flyback
    from heaviside.types._generated.topologies.forward import Forward as Forward
    from heaviside.types._generated.topologies.four_switch_buck_boost import (
        FourSwitchBuckBoost as FourSwitchBuckBoost,
    )
    from heaviside.types._generated.topologies.isolated_buck import (
        IsolatedBuck as IsolatedBuck,
    )
    from heaviside.types._generated.topologies.isolated_buck_boost import (
        IsolatedBuckBoost as IsolatedBuckBoost,
    )
    from heaviside.types._generated.topologies.llc_resonant import (
        LlcResonant as LlcResonant,
    )
    from heaviside.types._generated.topologies.phase_shifted_full_bridge import (
        PhaseShiftedFullBridge as PhaseShiftedFullBridge,
    )
    from heaviside.types._generated.topologies.phase_shifted_half_bridge import (
        PhaseShiftedHalfBridge as PhaseShiftedHalfBridge,
    )
    from heaviside.types._generated.topologies.power_factor_correction import (
        PowerFactorCorrection as PowerFactorCorrection,
    )
    from heaviside.types._generated.topologies.push_pull import PushPull as PushPull
    from heaviside.types._generated.topologies.sepic import Sepic as Sepic
    from heaviside.types._generated.topologies.series_resonant import (
        SeriesResonant as SeriesResonant,
    )
    from heaviside.types._generated.topologies.vienna import Vienna as Vienna
    from heaviside.types._generated.topologies.weinberg import Weinberg as Weinberg
    from heaviside.types._generated.topologies.zeta import Zeta as Zeta


def __getattr__(name: str) -> Any:
    """Lazily import the generated class; fail loudly if not generated."""
    try:
        module_rel = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    try:
        module = importlib.import_module(f"{__name__}.{module_rel}")
    except ModuleNotFoundError as exc:
        raise ImportError(
            f"heaviside.types.{name} is generated code that is missing on disk. "
            "Run `make types` (requires quicktype: `npm install -g quicktype`)."
        ) from exc
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(_EXPORTS)
