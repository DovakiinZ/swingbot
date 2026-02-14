from abc import ABC, abstractmethod
from typing import List, Optional
from core.types import Order, Position, Signal, Candle

class Broker(ABC):
    @abstractmethod
    def get_balance(self) -> float:
        pass

    @abstractmethod
    def place_order(self, signal: Signal, size: float) -> Optional[Order]:
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        pass

    @abstractmethod
    def get_open_orders(self) -> List[Order]:
        pass
    
    @abstractmethod
    def get_open_position(self) -> Optional[Position]:
        pass

    @abstractmethod
    def sync(self):
        pass
