from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SalesPerformance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    main_package_offered: int = Field(
        default=0,
        description="[System Prompt: Additional Category 1.1] Main Package Offered - Count of distinct main packages offered by the agent. The main package replaces or upgrades the customer's current package. Same package mentioned multiple times = 1 offer.",
    )

    main_package_accepted: int = Field(
        default=0,
        description="[System Prompt: Additional Category 1.2] Main Package Accepted - Count of distinct main packages accepted by the customer. Requires identity verification per Sub-Category 1.2.",
    )

    upsell_add_on_package_offered: int = Field(
        default=0,
        description="[System Prompt: Additional Category 1.3] Upsell Add-On Package Offered - Count of distinct upsell add-on packages offered. Upsell = SAME product category as main package. A package in a DIFFERENT category is cross-sell, not upsell.",
    )

    upsell_add_on_package_accepted: int = Field(
        default=0,
        description="[System Prompt: Additional Category 1.4] Upsell Add-On Package Accepted - Count of distinct upsell add-on packages accepted by the customer. Requires identity verification per Sub-Category 1.2.",
    )

    crosssell_add_on_product_offered: int = Field(
        default=0,
        description="[System Prompt: Additional Category 1.5] Cross-Sell Package Offered - Count of distinct cross-sell packages offered. Cross-sell = DIFFERENT product category from main package. Count individual packages, not categories. Must be 0 if crosssell_add_on_product_offered_list is empty.",
    )

    crosssell_add_on_product_accepted: int = Field(
        default=0,
        description="[System Prompt: Additional Category 1.6] Cross-Sell Package Accepted - Count of distinct cross-sell packages accepted by the customer. Must be 0 if crosssell_add_on_product_offered_list is empty. Requires identity verification per Sub-Category 1.2.",
    )

    main_and_upsell_add_on_product_offered_list: list[Literal["Mobile", "TOL", "TVS"]] = Field(
        default_factory=list,
        description="[System Prompt: Additional Category 1.7] Product categories for main package and upsell add-ons. Includes the main package's category. Must NOT overlap with crosssell_add_on_product_offered_list. Tags: 'Mobile', 'TOL', 'TVS'.",
    )

    crosssell_add_on_product_offered_list: list[Literal["Mobile", "TOL", "TVS"]] = Field(
        default_factory=list,
        description="[System Prompt: Additional Category 1.8] Product categories for cross-sell packages. Must be a DIFFERENT category from the main package. Must NEVER contain the same category as main_and_upsell_add_on_product_offered_list. Tags: 'Mobile', 'TOL', 'TVS'.",
    )

    @model_validator(mode="after")
    def dedup_list(self):
        """
        Step 1: Remove duplicate entries from the product offered lists.
        """
        self.crosssell_add_on_product_offered_list = list(set(self.crosssell_add_on_product_offered_list))
        self.main_and_upsell_add_on_product_offered_list = list(set(self.main_and_upsell_add_on_product_offered_list))
        return self

    @model_validator(mode="after")
    def enforce_mutual_exclusion(self):
        """
        Step 2: Enforce that main/upsell and cross-sell product lists have no overlapping
        categories. If a category appears in both lists, remove it from the cross-sell list
        since the main package's category defines the upsell boundary.
        """
        if self.main_and_upsell_add_on_product_offered_list and self.crosssell_add_on_product_offered_list:
            main_categories = set(self.main_and_upsell_add_on_product_offered_list)
            self.crosssell_add_on_product_offered_list = [
                cat for cat in self.crosssell_add_on_product_offered_list if cat not in main_categories
            ]
        return self

    @model_validator(mode="after")
    def product_list_empty(self):
        """
        Step 3 (runs last): Reset counts to 0 if the corresponding product offered list is
        empty. This catches both originally-empty lists and lists emptied by mutual exclusion.
        """
        if not self.crosssell_add_on_product_offered_list:
            self.crosssell_add_on_product_offered = 0
            self.crosssell_add_on_product_accepted = 0

        if not self.main_and_upsell_add_on_product_offered_list:
            self.main_package_offered = 0
            self.main_package_accepted = 0
            self.upsell_add_on_package_offered = 0
            self.upsell_add_on_package_accepted = 0
        return self
