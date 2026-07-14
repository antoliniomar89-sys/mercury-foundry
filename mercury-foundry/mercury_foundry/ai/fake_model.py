"""FakeModel — simulazione deterministica, dichiarata esplicitamente come tale.

NON è un vero modello AI: non fa alcuna chiamata di rete e non genera testo
"intelligente". Applica regole fisse basate sul testo del task per produrre
piani e patch, ESCLUSIVAMENTE per poter eseguire ed esercitare realmente il
ciclo SPEC->PLAN->BUILD->TEST->FIX->VERIFY->CANDIDATE quando non è disponibile
un provider AI reale (nessuna API key configurata in questa istanza).

Per dimostrare che il ciclo FIX è reale (non messo in scena), il primo
tentativo per la capability "health check" produce deliberatamente una
versione con un bug noto (chiave sbagliata nel payload), cosicché il test
reale fallisca al tentativo 1. Dal tentativo 2 in poi produce la versione
corretta. Il fallimento e la correzione sono verificati da una esecuzione
pytest reale, non simulata.
"""

from __future__ import annotations

from mercury_foundry.ai.provider import AIProvider, FileChange, PatchProposal

HEALTH_CHECK_MODULE = "health.py"
HEALTH_CHECK_TEST = "tests/test_health.py"


class FakeModel(AIProvider):
    name = "fake-deterministic"
    is_simulated = True

    def propose_plan(self, goal_description: str) -> list[str]:
        text = goal_description.lower()
        if "health check" in text:
            return [
                "Implementare la capability di health check (funzione + comando CLI) con test automatici reali",
            ]
        return [
            "Analizzare la richiesta e implementare la modifica minima necessaria con test automatici reali",
        ]

    def propose_patch(self, task_description: str, context: dict) -> PatchProposal:
        text = task_description.lower()
        attempt_number = int(context.get("attempt_number", 1))

        if "health check" in text:
            return self._health_check_patch(attempt_number)

        # Fallback generico per task non riconosciuti: crea un modulo placeholder
        # con un test reale che verifica solo che il modulo sia importabile.
        module_content = (
            '"""Modulo generato dal FakeModel (simulazione deterministica)."""\n\n'
            "def placeholder() -> str:\n"
            '    return "not-implemented"\n'
        )
        test_content = (
            "import capability\n\n\n"
            "def test_module_is_importable():\n"
            "    assert hasattr(capability, \"placeholder\")\n"
        )
        return PatchProposal(
            summary="Patch generica placeholder (task non riconosciuto dal FakeModel)",
            files=[FileChange(path="capability.py", content=module_content)],
            test_files=[FileChange(path="tests/test_capability.py", content=test_content)],
            provider_name=self.name,
            is_simulated=True,
        )

    def _health_check_patch(self, attempt_number: int) -> PatchProposal:
        test_content = (
            "import health\n\n\n"
            "def test_check_health_returns_ok_status():\n"
            "    result = health.check_health()\n"
            "    assert result[\"status\"] == \"ok\"\n"
            "    assert \"checked_at\" in result\n"
        )

        if attempt_number == 1:
            # Versione con bug deliberato: usa la chiave "timestamp" invece di
            # "checked_at" richiesta dal test. Serve a esercitare un fallimento
            # REALE al primo tentativo, non simulato.
            module_content = (
                '"""Capability: health check (v1 - tentativo 1, con bug noto)."""\n\n'
                "from datetime import datetime, timezone\n\n\n"
                "def check_health() -> dict:\n"
                "    return {\n"
                '        "status": "ok",\n'
                '        "timestamp": datetime.now(timezone.utc).isoformat(),\n'
                "    }\n\n\n"
                "if __name__ == \"__main__\":\n"
                "    import json\n\n"
                "    print(json.dumps(check_health()))\n"
            )
            summary = (
                "Tentativo 1: implementazione iniziale della capability health check "
                "(contiene un bug noto nel nome del campo di timestamp, usato per "
                "esercitare realmente il ciclo FIX)"
            )
        else:
            module_content = (
                '"""Capability: health check (corretta dopo fallimento reale dei test)."""\n\n'
                "from datetime import datetime, timezone\n\n\n"
                "def check_health() -> dict:\n"
                "    return {\n"
                '        "status": "ok",\n'
                '        "checked_at": datetime.now(timezone.utc).isoformat(),\n'
                "    }\n\n\n"
                "if __name__ == \"__main__\":\n"
                "    import json\n\n"
                "    print(json.dumps(check_health()))\n"
            )
            summary = (
                f"Tentativo {attempt_number}: correzione applicata dopo il fallimento reale "
                "dei test (campo rinominato da 'timestamp' a 'checked_at')"
            )

        return PatchProposal(
            summary=summary,
            files=[FileChange(path=HEALTH_CHECK_MODULE, content=module_content)],
            test_files=[FileChange(path=HEALTH_CHECK_TEST, content=test_content)],
            provider_name=self.name,
            is_simulated=True,
        )
