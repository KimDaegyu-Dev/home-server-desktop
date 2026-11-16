import unittest

from configure_kanban_toolsearch import add_deferred_tools


class ConfigureKanbanToolSearchTests(unittest.TestCase):
    def test_adds_kanban_and_retains_default_deferred_tools(self):
        config, changed = add_deferred_tools(
            {"model": {"default": "iq_3s"}},
            default_deferred=["todo_list", "computer_use"],
            kanban_tools=["kanban_create", "kanban_show"],
        )

        self.assertTrue(changed)
        self.assertEqual(
            config["tools"]["tool_search"]["defer"],
            ["todo_list", "computer_use", "kanban_create", "kanban_show"],
        )
        self.assertEqual(config["model"], {"default": "iq_3s"})

    def test_preserves_custom_deferrals_and_settings_idempotently(self):
        source = {"tools": {"tool_search": {
            "enabled": "on", "threshold_pct": 7, "defer": ["todo_list", "kanban_show"]
        }}}
        updated, changed = add_deferred_tools(
            source,
            default_deferred=["todo_list", "computer_use"],
            kanban_tools=["kanban_show", "kanban_create"],
        )

        self.assertTrue(changed)
        self.assertEqual(updated["tools"]["tool_search"]["enabled"], "on")
        self.assertEqual(updated["tools"]["tool_search"]["threshold_pct"], 7)
        self.assertEqual(
            updated["tools"]["tool_search"]["defer"],
            ["todo_list", "kanban_show", "kanban_create"],
        )
        again, changed_again = add_deferred_tools(
            updated,
            default_deferred=["todo_list", "computer_use"],
            kanban_tools=["kanban_show", "kanban_create"],
        )
        self.assertFalse(changed_again)
        self.assertEqual(again, updated)
        self.assertEqual(source["tools"]["tool_search"]["defer"], ["todo_list", "kanban_show"])

    def test_respects_explicitly_disabled_tool_search(self):
        for value in (False, {"enabled": "off", "defer": []}, {"enabled": "no", "defer": []}):
            updated, changed = add_deferred_tools(
                {"tools": {"tool_search": value}},
                default_deferred=["todo_list"],
                kanban_tools=["kanban_show"],
            )
            self.assertFalse(changed)
            self.assertEqual(updated["tools"]["tool_search"], value)

    def test_rejects_invalid_custom_defer_list(self):
        with self.assertRaisesRegex(ValueError, "must be a list"):
            add_deferred_tools(
                {"tools": {"tool_search": {"defer": "todo_list"}}},
                default_deferred=[],
                kanban_tools=["kanban_show"],
            )


if __name__ == "__main__":
    unittest.main()
