using AlexBot.Configuration;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace AlexBot.Services;

/// <summary>
/// Placeholder for the Teams real-time media plumbing. This is where you will:
/// - Handle incoming call notifications (Graph calling webhooks)
/// - Negotiate the media socket
/// - Pipe audio to SpeechService and push synthesized audio back
/// The Graph Communications SDK or future calling SDK should be used here.
/// </summary>
public class MeetingMediaRouter
{
    private readonly AlexOptions _options;
    private readonly SpeechService _speech;
    private readonly ILogger<MeetingMediaRouter> _logger;

    public MeetingMediaRouter(IOptions<AlexOptions> options, SpeechService speech, ILogger<MeetingMediaRouter> logger)
    {
        _options = options.Value;
        _speech = speech;
        _logger = logger;
    }

    public Task HandleIncomingCallAsync(string callId, CancellationToken cancellationToken)
    {
        _logger.LogInformation("Received incoming call {CallId}. This is where you accept and negotiate media.", callId);
        return Task.CompletedTask;
    }

    public Task ProcessAudioFrameAsync(byte[] audioBuffer, CancellationToken cancellationToken)
    {
        // Hook the buffer to Azure Speech via SpeechService and then decide how/when to respond.
        _logger.LogDebug("Processing audio buffer of {Length} bytes", audioBuffer.Length);
        return Task.CompletedTask;
    }
}
