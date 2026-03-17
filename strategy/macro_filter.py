from typing import List, Optional

def compute_macro_risk_scale(probabilities: List[float]) -> float:
    """
    Combine configured markets probabilities into a single risk scale.
    Strategy: 
       - Take the average probability if multiple markets.
       - Apply conservative mapping:
         p < 0.35 => 0.5
         0.35 <= p < 0.50 => 0.8
         p >= 0.50 => 1.0 (Normal Risk)
    
    If list is empty, return 1.0 (Neutral/No Filter).
    """
    if not probabilities:
        return 1.0
        
    avg_p = sum(probabilities) / len(probabilities)
    
    if avg_p < 0.35:
        return 0.5
    elif avg_p < 0.50:
        return 0.8
    else:
        return 1.0
