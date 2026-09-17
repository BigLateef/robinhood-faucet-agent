import json
import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(__file__))
import agent


STATUS_TEMPLATE = {
    "t": 10,
    "i": 0,
    "p": {
        "k": ["result", "error", "context"],
        "v": [
            {
                "t": 10,
                "i": 1,
                "p": {
                    "k": [
                        "configured",
                        "missing",
                        "payoutWei",
                        "cooldownHours",
                        "faucetAddress",
                        "balanceWei",
                        "claimsRemaining",
                        "rpcOk",
                    ],
                    "v": [],
                },
                "o": 0,
            },
            {"t": 2, "s": 1},
            {"t": 11, "i": 3, "p": {"k": [], "v": []}, "o": 0},
        ],
        "o": 0,
    },
}


def status_payload(balance="500000000000000", remaining=1, rpc=True):
    data = STATUS_TEMPLATE.copy()
    data["p"] = json.loads(json.dumps(STATUS_TEMPLATE["p"]))
    data["p"]["v"][0]["p"]["v"] = [
        {"t": 2, "s": 2},
        {"t": 9, "i": 2, "a": [], "o": 0},
        {"t": 1, "s": "500000000000000"},
        {"t": 0, "s": 24},
        {"t": 1, "s": "0xFf1d3AE8A10659B5782C462ac4f9e0DBE6eB84b3"},
        {"t": 1, "s": str(balance)},
        {"t": 0, "s": remaining},
        {"t": 2, "s": 2 if rpc else 0},
    ]
    return json.dumps(data)


class AgentTests(unittest.TestCase):
    def test_status_parser_and_funded_transition(self):
        funded = agent.parse_status(status_payload())
        self.assertTrue(funded.funded)
        self.assertEqual(funded.balance_wei, 500000000000000)

        dry = agent.parse_status(status_payload(balance="134633702688916", remaining=0))
        self.assertFalse(dry.funded)

    def test_direct_click_link_is_in_refill_alert(self):
        status = agent.parse_status(status_payload())
        with patch.object(agent, "DISCORD_WEBHOOK_URL", "discord-webhook-test-token"):
            response = Mock()
            with patch.object(agent.requests, "post", return_value=response) as post:
                self.assertTrue(agent.notify_discord(status))
                body = post.call_args.kwargs["json"]["content"]
                self.assertIn("https://rhfaucet.fun/#claim", body)
                self.assertIn("Open faucet and claim", body)


if __name__ == "__main__":
    unittest.main()
