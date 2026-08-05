from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import (
    get_value_by_path,
    resolve_env,
)
from src.utils.file_utils import (
    load_yaml,
    load_yaml_str,
)
from src.utils.logger import Logger

logger = Logger(__name__)


def gemini_cost(api_type: str, model_list: list[str]) -> list[dict]:
    """
    Retrieve Gemini model cost details from SharePoint configuration.
    Parameters:
        api_type (str): The type of Gemini API (e.g., 'batch', 'stream').
        model_list (list[str]): List of model names to retrieve cost details for (e.g., ['gemini-2.5-flash']).
    Returns:
        list[dict]: A list of dictionaries containing cost details for each model.
    """
    try:
        common_config = load_yaml("config/common.yml")
        sharepoint_control = SharePointModule(
            client_id=resolve_env(get_value_by_path(common_config, "control.client_id")),
            client_secret=resolve_env(get_value_by_path(common_config, "control.client_secret")),
            tenant_id=resolve_env(get_value_by_path(common_config, "control.tenant_id")),
            site_domain=resolve_env(get_value_by_path(common_config, "control.site_domain")),
            site_path=resolve_env(get_value_by_path(common_config, "control.site_path")),
        )
        cost_file_path = resolve_env(get_value_by_path(common_config, "control.gemini_cost_path"))
        res = sharepoint_control.get_item_by_path(cost_file_path)
        cost_config = load_yaml_str(res.content.decode("utf-8"))[api_type]

        model_pricing = []
        for model in model_list:
            cost_detail = cost_config.get(model, {})
            if not cost_detail:
                pricing = {
                    "type": api_type,
                    "model": model,
                    "pricing_type": None,
                    "input_type": None,
                    "tokens": None,
                    "cost": None,
                    "cost_per_token": None,
                    "unit": None,
                }
                model_pricing.append(pricing)
                logger.warning(f"No cost detail found for model {model} in {api_type} config")
                continue
            for item in get_value_by_path(cost_detail, "pricing.input", []):
                pricing = {
                    "type": api_type,
                    "model": model,
                    "pricing_type": "input",
                    "input_type": item.get("type", ""),
                    "tokens": int(item.get("tokens", 0)),
                    "cost": float(item.get("cost", 0)),
                    "cost_per_token": float(float(item.get("cost", 0)) / int(item.get("tokens", 1))),
                    "unit": item.get("unit", ""),
                }
                model_pricing.append(pricing)
            for item in get_value_by_path(cost_detail, "pricing.output", []):
                pricing = {
                    "type": api_type,
                    "model": model,
                    "pricing_type": "output",
                    "input_type": item.get("type", ""),
                    "tokens": int(item.get("tokens", 0)),
                    "cost": float(item.get("cost", 0)),
                    "cost_per_token": float(float(item.get("cost", 0)) / int(item.get("tokens", 1))),
                    "unit": item.get("unit", ""),
                }
                model_pricing.append(pricing)
        return model_pricing
    except Exception as e:
        logger.error(f"Error retrieving Gemini cost details: {e}", exc_info=True)
        raise


def cal_gemini_cost(config: list[dict], model: str, pricing_type: str, input_type: str, tokens: int) -> float:
    """
    Calculate the cost for a given Gemini model based on usage.
    Parameters:
        config (list[dict]): List of cost configuration dictionaries.
        model (str): The model name (e.g., 'gemini-2.5-flash').
        pricing_type (str): The type of pricing ('input' or 'output').
        input_type (str): The type of input (e.g., 'text', 'audio', 'image', 'video').
        tokens (int): The number of tokens used.
    Returns:
        float: The calculated cost.
    """
    for item in config:
        if item["model"] == model and item["pricing_type"] == pricing_type and item["input_type"] == input_type:
            cost = (tokens / item["tokens"]) * item["cost"]
            return round(cost, 6)
    logger.warning(
        f"No matching cost configuration found for model {model}, pricing_type {pricing_type}, input_type {input_type}"
    )
    return 0.0
