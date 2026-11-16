"""Validate and normalize full scoring dictionary imports; no automatic application actions."""
import copy

KEYS = ("role", "skills", "duties", "negative")
WEIGHT_KEYS = ("role", "skills", "duties", "experience", "work_format", "salary")
DEFAULT_WEIGHTS = {"role": 25, "skills": 25, "duties": 25, "experience": 15, "work_format": 7, "salary": 3}
DEFAULT_PENALTIES = {"l1_primary": 40, "user_support": 15, "sales": 35, "senior": 20, "office_only": 10}
MAX_TERMS = 150


def validate_rules(rules):
    if not isinstance(rules, dict) or set(rules) - set(KEYS):
        raise ValueError("Словари должны содержать только role, skills, duties, negative.")
    cleaned = {}
    for key, terms in rules.items():
        if not isinstance(terms, list) or len(terms) > MAX_TERMS:
            raise ValueError(f"{key}: ожидается массив не более {MAX_TERMS} строк.")
        if any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 100 for v in terms):
            raise ValueError(f"{key}: термины должны быть строками длиной 1–100 символов.")
        cleaned[key] = list(dict.fromkeys(v.strip() for v in terms))
    return cleaned


def validate_weights(weights):
    if not isinstance(weights, dict) or set(weights) != set(WEIGHT_KEYS):
        raise ValueError("Веса должны содержать все шесть критериев.")
    if any(type(v) is not int or v < 0 or v > 100 for v in weights.values()):
        raise ValueError("Каждый вес должен быть целым числом от 0 до 100.")
    if sum(weights.values()) != 100:
        raise ValueError("Сумма весов должна равняться 100.")
    return dict(weights)


def validate_penalties(penalties):
    if not isinstance(penalties, dict) or set(penalties) != set(DEFAULT_PENALTIES):
        raise ValueError("Некорректные ключи штрафов.")
    if any(type(v) is not int or not 0 <= v <= 100 for v in penalties.values()):
        raise ValueError("Штрафы должны быть целыми числами от 0 до 100.")
    return dict(penalties)


def normalize_import(payload):
    if not isinstance(payload, dict):
        raise ValueError("Корень JSON должен быть объектом.")
    if payload.get("dictionary_version") != "1.0":
        raise ValueError("Поддерживается dictionary_version: 1.0.")
    if payload.get("mode", "shadow") != "shadow":
        raise ValueError("Разрешён только теневой режим.")
    search = payload.get("search_integration", {})
    if not isinstance(search, dict) or search.get("replace_search_query", False) or search.get("replace_existing_filters", False):
        raise ValueError("Импорт не должен заменять поиск и фильтры HH.")
    thresholds = payload.get("thresholds", {})
    if not isinstance(thresholds, dict) or thresholds.get("action_mode", "shadow") != "shadow":
        raise ValueError("Автоматические решения скоринга пока не поддерживаются.")
    high, review = thresholds.get("high", 90), thresholds.get("manual_review", 50)
    if type(high) is not int or type(review) is not int or not 0 <= review < high <= 100:
        raise ValueError("Пороги должны удовлетворять 0 ≤ manual_review < high ≤ 100.")
    rules = validate_rules(payload.get("rules"))
    weights = validate_weights(payload.get("weights", DEFAULT_WEIGHTS))
    # Proposed penalty entries in the v1 specification are descriptive; use explicit
    # numeric penalties only when supplied under the 'penalties' key.
    penalties = validate_penalties(payload.get("penalties", DEFAULT_PENALTIES))
    proposed = payload.get("proposed_penalties")
    if proposed is not None:
        mapping = {
            "predominant_user_l1": "l1_primary",
            "occasional_user_support": "user_support",
            "sales_or_customer_service_primary": "sales",
            "senior_lead_5plus_required": "senior",
            "office_only": "office_only",
        }
        if not isinstance(proposed, list) or len(proposed) != len(mapping):
            raise ValueError("Некорректный proposed_penalties.")
        converted = {}
        for item in proposed:
            if not isinstance(item, dict) or item.get("id") not in mapping or type(item.get("points")) is not int or not -100 <= item["points"] <= 0:
                raise ValueError("Некорректное правило штрафа.")
            converted[mapping[item["id"]]] = -item["points"]
        if set(converted) != set(mapping.values()):
            raise ValueError("Неполный список штрафов.")
        if "penalties" not in payload:
            penalties = validate_penalties(converted)
    target = payload.get("target", {})
    if not isinstance(target, dict):
        raise ValueError("target должен быть объектом.")
    return {
        "rules": rules, "weights": weights, "penalties": penalties,
        "auto_threshold": high, "review_threshold": review,
        "target": copy.deepcopy(target),
        "dictionary_version": "1.0",
    }
