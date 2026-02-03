using Azure;
using Azure.AI.OpenAI;
using AlexBot.Configuration;
using Microsoft.Extensions.Options;

namespace AlexBot.Services;

public class LlmService
{
    private readonly OpenAIClient _client;
    private readonly AlexOptions _options;

    public LlmService(IOptions<AlexOptions> options)
    {
        _options = options.Value;

        if (string.IsNullOrWhiteSpace(_options.OpenAiEndpoint) || string.IsNullOrWhiteSpace(_options.OpenAiKey))
        {
            // Use a placeholder client to avoid null refs; actual calls will throw until configured.
            _client = new OpenAIClient(new Uri("https://example.openai.azure.com/"), new AzureKeyCredential("dummy"));
        }
        else
        {
            _client = new OpenAIClient(new Uri(_options.OpenAiEndpoint), new AzureKeyCredential(_options.OpenAiKey));
        }
    }

    public async Task<string> GetReplyAsync(string userMessage, CancellationToken cancellationToken)
    {
        var deployment = _options.OpenAiDeployment ?? "gpt-4o";
        var systemPrompt = $"You are {_options.PersonaName ?? "Alex"}, a concise, helpful teammate. Speak only when addressed.";

        var messages = new List<ChatMessage>
        {
            new ChatMessage(ChatRole.System, systemPrompt),
            new ChatMessage(ChatRole.User, userMessage)
        };

        try
        {
            var request = new ChatCompletionsOptions();
            foreach (var msg in messages)
            {
                request.Messages.Add(msg);
            }

            var response = await _client.GetChatCompletionsAsync(deployment, request, cancellationToken);
            var choice = response.Value.Choices.FirstOrDefault();
            return choice?.Message.Content?.Trim() ?? "I do not have a response right now.";
        }
        catch (Exception ex)
        {
            return $"Alex hit an error reaching the LLM: {ex.Message}";
        }
    }
}
