# -*- coding: utf-8 -*-
import base64

_A1 = "REk0TWpZeU16QTBNRFE0UEM5QlJFVk5VMU5SUFQwPQ=="
_A2 = "L1RVa0ZSUWpFPQ=="
_B1 = "TmpVMk1qVXpNakUxT0M4RlFVeFRRMUZ4TWpWbE1qWmxhVkl6"
_B2 = "UVdFeE5UazFUVVU9"
_C1 = "T0RVMU5EVXpNRGMyT0M4R1JVRk1SVWxqVFVaSlRVa3hNbGxR"
_C2 = "UlRoQk1FWT0="
_D1 = "TnpFeE5EZ3lNak0yTWk4R1JVRkpSVU5sUVdKSlFqWXlNekEx"
_D2 = "UlRJeFZWTXhNdz09"
_E1 = "TnpneE1USTVOekl5TWk4R1JVRkpSVU5sUVhObE1XTXhNakkx"
_E2 = "Ulhra1VUSkJOVDA9"
_F1 = "TnpFeE1ETXhOekUxTWk4R1JVRkpSVU5sUVdGTE1EYzFNakl6"
_F2 = "UlZSRU5URTBUVDA9"
_G1 = "T0RVNE56SXdNRFl6T0M4R1JVRkpSVU5sUVhSa01UVXhOakl5"
_G2 = "UlhWc1JqWXhORDA9"
_H1 = "T0RVeU5qSTJOVFUyTWk4R1JVRkpSVU5sUVhJMU1URTBOVFE1"
_H2 = "UlRaRk1qVTJNdz09"
_I1 = "T0RVMU5UQTJNVGt5TVM4R1JVRkpSVU5sUVhRd1JqWXhNak14"
_I2 = "UlhwTlVtWlRPVDA9"
_J1 = "T0Rjd056STBNREUzT0M4R1JVRkpSVU5sUVhRMU5EazBNakUz"
_J2 = "UlV4VFFrWlJSVDQ9"

_TOKENS_PARTS = [
    ["_A1", "_A2"],
    ["_B1", "_B2"],
    ["_C1", "_C2"],
    ["_D1", "_D2"],
    ["_E1", "_E2"],
    ["_F1", "_F2"],
    ["_G1", "_G2"],
    ["_H1", "_H2"],
    ["_I1", "_I2"],
    ["_J1", "_J2"],
]

CTRL_PART1 = "NzcyNDkwMzQ5"
CTRL_PART2 = "MzAwMS0="
VAULT_PART1 = "MjY3NTE3Nzc1"
VAULT_PART2 = "MzAwMS0="
SECRET_PART1 = "QDMyMUAz"
SECRET_PART2 = "MjFuZWFa"
SECRET_PART3 = ""

def _reverse(s):
    return s[::-1]

def _b64_decode(s):
    return base64.b64decode(s).decode()

def _assemble_token(parts):
    raw = ''.join(eval(p) for p in parts)
    return _reverse(_b64_decode(raw))

def _assemble_int(parts):
    return int(_assemble_token(parts))

def load_config():
    tokens = [_assemble_token(parts) for parts in _TOKENS_PARTS]
    active = tokens[:6]
    reserve = tokens[6:10]
    ctrl = _assemble_int(['CTRL_PART1', 'CTRL_PART2'])
    vault = _assemble_int(['VAULT_PART1', 'VAULT_PART2'])
    secret = _assemble_token(['SECRET_PART1', 'SECRET_PART2', 'SECRET_PART3'])
    return active, reserve, ctrl, vault, secret
