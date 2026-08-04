"""Product service — unrelated to auth, used for retrieval quality testing."""


class ProductService:
    """Handles product catalog operations."""

    def __init__(self):
        self._products: dict = {}

    def add_product(self, product_id: str, name: str, price: float) -> dict:
        """Add a new product to the catalog."""
        product = {
            "id": product_id,
            "name": name,
            "price": price,
            "available": True,
        }
        self._products[product_id] = product
        return product

    def get_product(self, product_id: str) -> dict | None:
        """Get a product by ID."""
        return self._products.get(product_id)

    def search_products(self, query: str) -> list:
        """Search products by name."""
        query_lower = query.lower()
        return [
            p for p in self._products.values()
            if query_lower in p["name"].lower()
        ]


class PricingEngine:
    """Calculates product pricing with discounts."""

    def __init__(self):
        self._discounts: dict = {}

    def set_discount(self, product_id: str, percentage: float) -> None:
        """Set a discount percentage for a product."""
        self._discounts[product_id] = percentage

    def calculate_price(self, base_price: float, product_id: str | None = None) -> float:
        """Calculate final price with applicable discount."""
        discount = self._discounts.get(product_id, 0.0) if product_id else 0.0
        return base_price * (1 - discount / 100)
