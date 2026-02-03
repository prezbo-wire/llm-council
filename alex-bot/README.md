# Alex (Teams Calling Copilot) — Skeleton

This folder contains a starter layout for a Teams real-time media bot (“Alex”). It is intentionally small: HTTP bot endpoint + stubs for LLM, Speech, and media routing, plus a manifest template.

## Structure
- `src/AlexBot/` — ASP.NET Core Bot Framework host (net8.0)
  - `Bots/AlexBot.cs` — basic text responder wired to LLM stub
  - `Services/MeetingMediaRouter.cs` — placeholder for real-time media socket handling
  - `Services/SpeechService.cs` — Azure Speech STT/TTS helper
  - `Services/LlmService.cs` — Azure OpenAI helper (persona-aware)
  - `Adapters/AdapterWithErrorHandler.cs` — Bot Framework adapter with error logging
  - `Controllers/BotController.cs` — `/api/messages` endpoint
  - `appsettings.json` — config placeholders
- `manifest/manifest.json` — Teams app manifest template (update IDs, icons)

## Quickstart (text-only bot)
1) Install .NET 8 SDK and the Bot Framework emulator prerequisites.
2) Fill `appsettings.json` (OpenAI endpoint/key/deployment, optional Speech/Search, bot base URL).
3) Restore & run locally:
   ```bash
   cd alex-bot/src/AlexBot
   dotnet restore
   dotnet run
   ```
4) Use `ngrok http https://localhost:5001` (or your tunneling choice) and set the `BotBaseUrl` to `https://<ngrok-domain>/api/messages`.
5) In Azure → Bot Channels Registration: set the messaging endpoint to the same URL.
6) Sideload the app in Teams using the manifest (zip the `manifest/` folder + two 32x32/192x192 icons).

## Enabling meetings & calling
- Azure AD app needs delegated/application Graph perms: `Calls.AccessMedia.All`, `Calls.JoinGroupCall.AsGuest`, `OnlineMeetings.Read.All`, `User.Read.All` (admin consent).
- Host on AKS or Azure Container Apps; App Service cannot host real-time media sockets reliably.
- Implement media in `MeetingMediaRouter`: accept call notifications, negotiate the audio socket, pipe PCM to `SpeechService.TranscribeAsync`, trigger `LlmService`, and push synthesized audio back to the socket.
- Add trigger detection (e.g., regex for “Alex, what do you think?”) before speaking.
- Post the text reply to chat alongside audio for accessibility.

## Teams manifest notes
- Update `id`, `botId`, `webApplicationInfo.id`, `resource`, `validDomains`, and icons.
- Keep `scopes` including `meeting` so Alex can be invited to calls.
- Validate the manifest in Teams Developer Portal; toggle `supportsCalling`/`supportsVideo` as your tenant requires.

## Next implementation steps
- Wire Graph Calling webhooks → `MeetingMediaRouter`.
- Maintain a rolling transcript buffer (2–5 min) to feed the LLM.
- Add Azure AI Search retrieval for internal docs (inject into `LlmService` prompts).
- Add telemetry (App Insights) and cost/latency metrics.
