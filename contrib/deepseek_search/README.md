# deepseek_search (contrib plugin)

Declares DeepSeek's server-side web tools (`deepseek_web_search`, `deepseek_read_url`) on
main-model requests to `api.deepseek.com`, so DeepSeek can search and read pages itself.
Hermes ignores the extra `annotations` / `server_tool_calls` fields in the reply.

Not a bundled plugin: it lives here for version control only. Install it as a user plugin:

    cp -r contrib/deepseek_search "$HERMES_HOME/plugins/"
    hermes plugins enable deepseek_search      # hot-reloads into a running gateway

Rules it follows:

- only requests whose base URL is a deepseek.com host;
- never cron runs (their tool lists are set per job on purpose);
- never a request with thinking turned off. DeepSeek rejects client tools combined with
  `deepseek_web_search` outside thinking mode (HTTP 400), and voice-message turns run with
  thinking off unless `auxiliary.voice_chat.reasoning_effort` is set.

Searches are billed per call on the DeepSeek account. Turn it off with
`hermes plugins disable deepseek_search`.

Tests: `python -m pytest contrib/deepseek_search`.
