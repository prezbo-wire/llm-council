namespace AlexBot.Configuration;

public class AlexOptions
{
    public string? PersonaName { get; set; }
    public string? OpenAiEndpoint { get; set; }
    public string? OpenAiKey { get; set; }
    public string? OpenAiDeployment { get; set; }
    public string? SpeechRegion { get; set; }
    public string? SpeechKey { get; set; }
    public string? SearchEndpoint { get; set; }
    public string? SearchKey { get; set; }
    public string? SearchIndex { get; set; }
    public string? TenantId { get; set; }
    public string? ClientId { get; set; }
    public string? ClientSecret { get; set; }
    public string? BotBaseUrl { get; set; }
}
