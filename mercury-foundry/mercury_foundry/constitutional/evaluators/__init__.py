"""Evaluator separati per principio costituzionale — MF-CONST-001.

Ogni evaluator è responsabile di un singolo principio e riceve in input
esclusivamente dati strutturati verificabili. Nessun LLM, nessuna euristica
ambigua: solo condizioni deterministiche su campi presenti nella richiesta.
"""
