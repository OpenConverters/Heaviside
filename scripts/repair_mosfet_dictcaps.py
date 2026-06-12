#!/usr/bin/env python3
"""One-off repair of TAS mosfet rows whose capacitance fields are dict-shaped
({"typical": x[, "maximum": y]}) where the SAS schema wants a plain number.

61 such fields existed (all Infineon, all with inputCapacitance == outputCapacitance
identical dicts -- a scrape artifact, so the stored numbers themselves were suspect).
Every replacement value below was read literally from the part's Infineon datasheet
(fetched 2026-06-12, URL per part; evidence = the literal pdftotext table line).
Fields whose datasheet line could not be parsed unambiguously are LEFT UNTOUCHED
(still dict-shaped) and listed by the script at the end of the run.

Rule: a dict-shaped field is replaced ONLY when this table has a datasheet value
for that exact part+field; the dict is replaced by the plain typical-value number.
All other rows stay byte-identical.

Run from the Heaviside repo root:
    .venv-web/bin/python scripts/repair_mosfet_dictcaps.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "TAS" / "data" / "mosfets.ndjson"

CAP_FIELDS = ("inputCapacitance", "outputCapacitance", "reverseTransferCapacitance")

# part reference -> datasheet source + per-field literal values
TABLE: dict = json.loads(r"""{
 "IAUAN04S7N012NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iauan04s7n012-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 2.73e-09,
    "evidence": "Input capacitance 6) Ciss 2730 3550 [VDS = 20 V, VGS = 0 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.5899999999999999e-09,
    "evidence": "Output capacitance 6) Coss - 1590 2070 pF VDS = 20 V, VGS = 0 V, f = 1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 5.4e-11,
    "evidence": "Reverse transfer capacitance 6) Crss 54 81 [VDS = 20 V, VGS = 0 V, f = 1 MHz]"
   }
  }
 },
 "ISC025N08NM6NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-isc025n08nm6-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 3.2999999999999998e-09,
    "evidence": "Input capacitance 7) Ciss 3300 4000 [VGS=0 V, VDS=40 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.1e-09,
    "evidence": "Output capacitance 7) Coss - 1100 1380 pF VGS=0 V, VDS=40 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 2.8e-11,
    "evidence": "Reverse transfer capacitance 7) Crss 28 39 [VGS=0 V, VDS=40 V, f=1 MHz]"
   }
  }
 },
 "ISC025N08NM6SCNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-isc025n08nm6sc-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 3.2999999999999998e-09,
    "evidence": "Input capacitance 7) Ciss 3300 4000 [VGS=0 V, VDS=40 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.1e-09,
    "evidence": "Output capacitance 7) Coss - 1100 1380 pF VGS=0 V, VDS=40 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 2.8e-11,
    "evidence": "Reverse transfer capacitance 7) Crss 28 39 [VGS=0 V, VDS=40 V, f=1 MHz]"
   }
  }
 },
 "IPF014N10NM8NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipf014n10nm8-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1e-08,
    "evidence": "Input capacitance 7) Ciss 10000 13000 [VGS=0 V, VDS=50 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.5e-09,
    "evidence": "Output capacitance 7) Coss - 1500 2000 pF VGS=0 V, VDS=50 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 2.9e-10,
    "evidence": "Reverse transfer capacitance 7) Crss 290 510 [VGS=0 V, VDS=50 V, f=1 MHz]"
   }
  }
 },
 "IPT014N10NM8NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipt014n10nm8-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1e-08,
    "evidence": "Input capacitance 7) Ciss 10000 13000 [VGS=0 V, VDS=50 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.5e-09,
    "evidence": "Output capacitance 7) Coss - 1500 2000 pF VGS=0 V, VDS=50 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 2.9e-10,
    "evidence": "Reverse transfer capacitance 7) Crss 290 510 [VGS=0 V, VDS=50 V, f=1 MHz]"
   }
  }
 },
 "IPF009N10NM8NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipf009n10nm8-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1.6e-08,
    "evidence": "Input capacitance 7) Ciss 16000 21000 [VGS=0 V, VDS=50 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 2.4e-09,
    "evidence": "Output capacitance 7) Coss - 2400 3100 pF VGS=0 V, VDS=50 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 4.5e-10,
    "evidence": "Reverse transfer capacitance 7) Crss 450 790 [VGS=0 V, VDS=50 V, f=1 MHz]"
   }
  }
 },
 "IPF019N10NM8NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipf019n10nm8-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 7.9e-09,
    "evidence": "Input capacitance 7) Ciss 7900 10000 [VGS=0 V, VDS=50 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.2e-09,
    "evidence": "Output capacitance 7) Coss - 1200 1600 pF VGS=0 V, VDS=50 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 2.2e-10,
    "evidence": "Reverse transfer capacitance 7) Crss 220 380 [VGS=0 V, VDS=50 V, f=1 MHz]"
   }
  }
 },
 "IPB018N10NM8NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipb018n10nm8-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1e-08,
    "evidence": "Input capacitance 7) Ciss 10000 13000 [VGS=0 V, VDS=50 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.5e-09,
    "evidence": "Output capacitance 7) Coss - 1500 2000 pF VGS=0 V, VDS=50 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 2.9e-10,
    "evidence": "Reverse transfer capacitance 7) Crss 290 510 [VGS=0 V, VDS=50 V, f=1 MHz]"
   }
  }
 },
 "IPB013N10NM8NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipb013n10nm8-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1.6e-08,
    "evidence": "Input capacitance 7) Ciss 16000 21000 [VGS=0 V, VDS=50 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 2.4e-09,
    "evidence": "Output capacitance 7) Coss - 2400 3100 pF VGS=0 V, VDS=50 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 4.5e-10,
    "evidence": "Reverse transfer capacitance 7) Crss 450 790 [VGS=0 V, VDS=50 V, f=1 MHz]"
   }
  }
 },
 "IPT018N10NM8NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipt018n10nm8-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 7.9e-09,
    "evidence": "Input capacitance 7) Ciss 7900 10000 [VGS=0 V, VDS=50 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.2e-09,
    "evidence": "Output capacitance 7) Coss - 1200 1600 pF VGS=0 V, VDS=50 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 2.2e-10,
    "evidence": "Reverse transfer capacitance 7) Crss 220 380 [VGS=0 V, VDS=50 V, f=1 MHz]"
   }
  }
 },
 "ISC040N10NM8NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-isc040n10nm8-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 3.1e-09,
    "evidence": "Input capacitance 7) Ciss 3100 4000 [VGS=0 V, VDS=50 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 4.6e-10,
    "evidence": "Output capacitance 7) Coss - 460 600 pF VGS=0 V, VDS=50 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 9.3e-11,
    "evidence": "Reverse transfer capacitance 7) Crss 93 160 [VGS=0 V, VDS=50 V, f=1 MHz]"
   }
  }
 },
 "ISC033N10NM8NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-isc033n10nm8-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 3.8e-09,
    "evidence": "Input capacitance 7) Ciss 3800 4900 [VGS=0 V, VDS=50 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 5.6e-10,
    "evidence": "Output capacitance 7) Coss - 560 730 pF VGS=0 V, VDS=50 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 1.1e-10,
    "evidence": "Reverse transfer capacitance 7) Crss 110 190 [VGS=0 V, VDS=50 V, f=1 MHz]"
   }
  }
 },
 "ISC019N10NM8NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-isc019n10nm8-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 6.8e-09,
    "evidence": "Input capacitance 7) Ciss 6800 8800 [VGS=0 V, VDS=50 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 1e-09,
    "evidence": "Output capacitance 7) Coss - 1000 1300 pF VGS=0 V, VDS=50 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 1.9e-10,
    "evidence": "Reverse transfer capacitance 7) Crss 190 330 [VGS=0 V, VDS=50 V, f=1 MHz]"
   }
  }
 },
 "IAUAN04S7N014NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iauan04s7n014-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 2.26e-09,
    "evidence": "Input capacitance 6) Ciss 2260 2930 [VDS = 20 V, VGS = 0 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.32e-09,
    "evidence": "Output capacitance 6) Coss - 1320 1710 pF VDS = 20 V, VGS = 0 V, f = 1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 4.5999999999999996e-11,
    "evidence": "Reverse transfer capacitance 6) Crss 46 69 [VDS = 20 V, VGS = 0 V, f = 1 MHz]"
   }
  }
 },
 "IAUTN08S7N007NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iautn08s7n007-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1.3821e-08,
    "evidence": "Input capacitance C iss - 13821 17967 pF [GS = 0 V, V DS = 40 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 5.564e-09,
    "evidence": "Output capacitance C oss V GS = 0 V, V DS = 40 V, f = 1 MHz - 5564 7233"
   },
   "reverseTransferCapacitance": {
    "value": 5.4e-11,
    "evidence": "Reverse transfer capacitance C rss - 54 81 [GS = 0 V, V DS = 40 V, f = 1 MHz]"
   }
  }
 },
 "IMDQ75R011M2HNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-imdq75r011m2h-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 3.689e-09,
    "evidence": "Input capacitance Ciss 3689 - [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   },
   "reverseTransferCapacitance": {
    "value": 1.9e-11,
    "evidence": "Reverse transfer capacitance Crss - 19 - pF VGS = 0 V, VDS = 500 V, f = 250 kHz"
   },
   "outputCapacitance": {
    "value": 2.45e-10,
    "evidence": "Output capacitance 8) Coss 245 318 [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   }
  }
 },
 "AIMDQ75R011M2HNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-aimdq75r011m2h-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 3.689e-09,
    "evidence": "Input capacitance Ciss 3689 - [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   },
   "reverseTransferCapacitance": {
    "value": 1.9e-11,
    "evidence": "Reverse transfer capacitance Crss - 19 - pF VGS = 0 V, VDS = 500 V, f = 250 kHz"
   },
   "outputCapacitance": {
    "value": 2.45e-10,
    "evidence": "Output capacitance 8) Coss 245 318 [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   }
  }
 },
 "IMBG75R020M2HNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-imbg75r020m2h-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 2.085e-09,
    "evidence": "Input capacitance Ciss 2085 - [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   },
   "reverseTransferCapacitance": {
    "value": 1.0900000000000001e-11,
    "evidence": "Reverse transfer capacitance Crss - 10.9 - pF VGS = 0 V, VDS = 500 V, f = 250 kHz"
   },
   "outputCapacitance": {
    "value": 1.41e-10,
    "evidence": "Output capacitance 8) Coss 141 183 [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   }
  }
 },
 "IMBG75R011M2HNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-imbg75r011m2h-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 3.689e-09,
    "evidence": "Input capacitance Ciss 3689 - [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   },
   "reverseTransferCapacitance": {
    "value": 1.9e-11,
    "evidence": "Reverse transfer capacitance Crss - 19 - pF VGS = 0 V, VDS = 500 V, f = 250 kHz"
   },
   "outputCapacitance": {
    "value": 2.45e-10,
    "evidence": "Output capacitance 8) Coss 245 318 [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   }
  }
 },
 "IMBG75R016M2HNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-imbg75r016m2h-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 2.577e-09,
    "evidence": "Input capacitance Ciss 2577 - [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   },
   "reverseTransferCapacitance": {
    "value": 1.35e-11,
    "evidence": "Reverse transfer capacitance Crss - 13.5 - pF VGS = 0 V, VDS = 500 V, f = 250 kHz"
   },
   "outputCapacitance": {
    "value": 1.73e-10,
    "evidence": "Output capacitance 8) Coss 173 225 [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   }
  }
 },
 "AIMBG75R020M2HNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-aimbg75r020m2h-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 2.085e-09,
    "evidence": "Input capacitance Ciss 2085 - [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   },
   "reverseTransferCapacitance": {
    "value": 1.0900000000000001e-11,
    "evidence": "Reverse transfer capacitance Crss - 10.9 - pF VGS = 0 V, VDS = 500 V, f = 250 kHz"
   },
   "outputCapacitance": {
    "value": 1.41e-10,
    "evidence": "Output capacitance 8) Coss 141 183 [VGS = 0 V, VDS = 500 V, f = 250 kHz]"
   }
  }
 },
 "IPT009N10NM8NEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-ipt009n10nm8-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1.6e-08,
    "evidence": "Input capacitance 7) Ciss 16000 21000 [VGS=0 V, VDS=50 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 2.4e-09,
    "evidence": "Output capacitance 7) Coss - 2400 3100 pF VGS=0 V, VDS=50 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 4.5e-10,
    "evidence": "Reverse transfer capacitance 7) Crss 450 790 [VGS=0 V, VDS=50 V, f=1 MHz]"
   }
  }
 },
 "IQE010N04LM7CGSCNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-iqe010n04lm7cgsc-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 3e-09,
    "evidence": "Input capacitance 7) Ciss 3000 3900 [VGS=0 V, VDS=20 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 9.8e-10,
    "evidence": "Output capacitance 7) Coss - 980 1300 pF VGS=0 V, VDS=20 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 5e-11,
    "evidence": "Reverse transfer capacitance 7) Crss 50 88 [VGS=0 V, VDS=20 V, f=1 MHz]"
   }
  }
 },
 "IQE010N04LM7CGNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/24/49/infineon-iqe010n04lm7cg-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 3e-09,
    "evidence": "Input capacitance 7) Ciss 3000 3900 [VGS=0 V, VDS=20 V, f=1 MHz]"
   },
   "outputCapacitance": {
    "value": 9.8e-10,
    "evidence": "Output capacitance 7) Coss - 980 1300 pF VGS=0 V, VDS=20 V, f=1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 5e-11,
    "evidence": "Reverse transfer capacitance 7) Crss 50 88 [VGS=0 V, VDS=20 V, f=1 MHz]"
   }
  }
 },
 "IAUCN04S7N015GNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn04s7n015g-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 2.643e-09,
    "evidence": "Input capacitance C iss - 2643 3436 pF [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.372e-09,
    "evidence": "Output capacitance C oss V GS = 0 V, V DS = 20 V, f = 1 MHz - 1372 1784"
   },
   "reverseTransferCapacitance": {
    "value": 4.5e-11,
    "evidence": "Reverse transfer capacitance C rss - 45 68 [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN04S7N019GNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn04s7n019g-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1.976e-09,
    "evidence": "Input capacitance C iss - 1976 2569 pF [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.0189999999999999e-09,
    "evidence": "Output capacitance C oss V GS = 0 V, V DS = 20 V, f = 1 MHz - 1019 1325"
   },
   "reverseTransferCapacitance": {
    "value": 3.9e-11,
    "evidence": "Reverse transfer capacitance C rss - 39 59 [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN04S7N027GNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn04s7n027g-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1.492e-09,
    "evidence": "Input capacitance C iss - 1492 1940 pF [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 7.61e-10,
    "evidence": "Output capacitance C oss V GS = 0 V, V DS = 20 V, f = 1 MHz - 761 990"
   },
   "reverseTransferCapacitance": {
    "value": 3.3e-11,
    "evidence": "Reverse transfer capacitance C rss - 33 50 [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN04S7N037GNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn04s7n037g-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1.059e-09,
    "evidence": "Input capacitance C iss - 1059 1377 pF [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 5.32e-10,
    "evidence": "Output capacitance C oss V GS = 0 V, V DS = 20 V, f = 1 MHz - 532 692"
   },
   "reverseTransferCapacitance": {
    "value": 2.9e-11,
    "evidence": "Reverse transfer capacitance C rss - 29 44 [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN04S7N047GNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn04s7n047g-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 8.239999999999999e-10,
    "evidence": "Input capacitance C iss - 824 1072 pF [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 4.07e-10,
    "evidence": "Output capacitance C oss V GS = 0 V, V DS = 20 V, f = 1 MHz - 407 530"
   },
   "reverseTransferCapacitance": {
    "value": 2.6e-11,
    "evidence": "Reverse transfer capacitance C rss - 26 39 [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN10S7L290TNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn10s7l290t-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 4.3e-10,
    "evidence": "Input capacitance 10) Ciss 430 560 [VDS = 50 V, VGS = 0 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.8e-10,
    "evidence": "Output capacitance 10) Coss - 180 240 pF VDS = 50 V, VGS = 0 V, f = 1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 5e-12,
    "evidence": "Reverse transfer capacitance 10) Crss 5 8 [VDS = 50 V, VGS = 0 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN10S5L110TNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn10s5l110t-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1.34e-09,
    "evidence": "Input capacitance 10) Ciss 1340 1740 [VDS = 50 V, VGS = 0 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 2.3e-10,
    "evidence": "Output capacitance 10) Coss - 230 300 pF VDS = 50 V, VGS = 0 V, f = 1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 1.2e-11,
    "evidence": "Reverse transfer capacitance 10) Crss 12 18 [VDS = 50 V, VGS = 0 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN08S5L160TNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn08s5l160t-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 8.1e-10,
    "evidence": "Input capacitance 10) Ciss 810 1050 [VDS = 40 V, VGS = 0 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.4e-10,
    "evidence": "Output capacitance 10) Coss - 140 190 pF VDS = 40 V, VGS = 0 V, f = 1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 1.0999999999999999e-11,
    "evidence": "Reverse transfer capacitance 10) Crss 11 17 [VDS = 40 V, VGS = 0 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN10S7N025TNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn10s7n025t-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 5.08e-09,
    "evidence": "Input capacitance 10) Ciss 5080 6600 [VDS = 50 V, VGS = 0 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 2.12e-09,
    "evidence": "Output capacitance 10) Coss - 2120 2760 pF VDS = 50 V, VGS = 0 V, f = 1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 2e-11,
    "evidence": "Reverse transfer capacitance 10) Crss 20 30 [VDS = 50 V, VGS = 0 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN08S7N036TNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn08s7n036t-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 2.4e-09,
    "evidence": "Input capacitance 10) Ciss 2400 3120 [VDS = 40 V, VGS = 0 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 9.8e-10,
    "evidence": "Output capacitance 10) Coss - 980 1280 pF VDS = 40 V, VGS = 0 V, f = 1 MHz"
   },
   "reverseTransferCapacitance": {
    "value": 1.5e-11,
    "evidence": "Reverse transfer capacitance 10) Crss 15 23 [VDS = 40 V, VGS = 0 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN04S7L018DNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn04s7l018d-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 2.843e-09,
    "evidence": "Input capacitance C iss - 2843 3696 pF [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 1.425e-09,
    "evidence": "Output capacitance C oss V GS = 0 V, V DS = 20 V, f = 1 MHz - 1425 1853"
   },
   "reverseTransferCapacitance": {
    "value": 5e-11,
    "evidence": "Reverse transfer capacitance C rss - 50 75 [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN04S7L024DNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn04s7l024d-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1.8e-09,
    "evidence": "Input capacitance C iss - 1800 2340 pF [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 9.01e-10,
    "evidence": "Output capacitance C oss V GS = 0 V, V DS = 20 V, f = 1 MHz - 901 1171"
   },
   "reverseTransferCapacitance": {
    "value": 3.3e-11,
    "evidence": "Reverse transfer capacitance C rss - 33 50 [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   }
  }
 },
 "IAUCN04S7L038DNEW": {
  "source": "https://www.infineon.com/assets/row/public/documents/10/49/infineon-iaucn04s7l038d-datasheet-en.pdf",
  "fields": {
   "inputCapacitance": {
    "value": 1.031e-09,
    "evidence": "Input capacitance C iss - 1031 1340 pF [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   },
   "outputCapacitance": {
    "value": 5.14e-10,
    "evidence": "Output capacitance C oss V GS = 0 V, V DS = 20 V, f = 1 MHz - 514 668"
   },
   "reverseTransferCapacitance": {
    "value": 2.1e-11,
    "evidence": "Reverse transfer capacitance C rss - 21 32 [GS = 0 V, V DS = 20 V, f = 1 MHz]"
   }
  }
 }
}""")


def main() -> int:
    out_lines: list[str] = []
    replaced = 0
    left: list[str] = []
    for lineno, line in enumerate(PATH.open(encoding="utf-8"), 1):
        row = json.loads(line)
        body = row.get("semiconductor", {}).get("mosfet", {})
        mi = body.get("manufacturerInfo") or {}
        el = (mi.get("datasheetInfo") or {}).get("electrical")
        ref = mi.get("reference")
        changed = False
        if isinstance(el, dict):
            for f in CAP_FIELDS:
                if not isinstance(el.get(f), dict):
                    continue
                ent = TABLE.get(ref, {}).get("fields", {}).get(f)
                if ent is None:
                    left.append(f"line {lineno} {ref}.{f} (no unambiguous datasheet value)")
                    continue
                el[f] = ent["value"]
                replaced += 1
                changed = True
        if changed:
            out_lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        else:
            out_lines.append(line.rstrip("\n"))
    tmp = PATH.with_suffix(".ndjson.dictcaps")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(PATH)
    print(f"replaced {replaced} dict-shaped capacitance fields with datasheet numbers")
    if left:
        print(f"left untouched ({len(left)}):")
        for x in left:
            print("  " + x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
