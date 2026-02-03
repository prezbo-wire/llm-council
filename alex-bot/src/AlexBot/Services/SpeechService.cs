using AlexBot.Configuration;
using Microsoft.CognitiveServices.Speech;
using Microsoft.CognitiveServices.Speech.Audio;
using Microsoft.Extensions.Options;

namespace AlexBot.Services;

public class SpeechService
{
    private readonly AlexOptions _options;

    public SpeechService(IOptions<AlexOptions> options)
    {
        _options = options.Value;
    }

    public async Task<string> TranscribeAsync(Stream audioStream, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(_options.SpeechKey) || string.IsNullOrWhiteSpace(_options.SpeechRegion))
        {
            return "[speech not configured]";
        }

        var config = SpeechConfig.FromSubscription(_options.SpeechKey, _options.SpeechRegion);
        using var audioInput = AudioConfig.FromStreamInput(new PullAudioInputStream(new BinaryAudioStreamReader(audioStream)));
        using var recognizer = new SpeechRecognizer(config, audioInput);

        var result = await recognizer.RecognizeOnceAsync().ConfigureAwait(false);
        return result.Text;
    }

    public async Task<Stream> SynthesizeAsync(string text, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(_options.SpeechKey) || string.IsNullOrWhiteSpace(_options.SpeechRegion))
        {
            return new MemoryStream();
        }

        var config = SpeechConfig.FromSubscription(_options.SpeechKey, _options.SpeechRegion);
        config.SpeechSynthesisVoiceName = "en-US-JasonNeural";

        using var synthesizer = new SpeechSynthesizer(config, (AudioConfig?)null);
        var result = await synthesizer.SpeakTextAsync(text).ConfigureAwait(false);

        var audio = result.AudioData ?? Array.Empty<byte>();
        var buffer = new MemoryStream(audio);
        buffer.Position = 0;
        return buffer;
    }
}

// Minimal audio stream reader; replace with push stream wired to the media socket.
internal class BinaryAudioStreamReader : PullAudioInputStreamCallback
{
    private readonly Stream _stream;

    public BinaryAudioStreamReader(Stream stream)
    {
        _stream = stream;
    }

    public override int Read(byte[] dataBuffer, uint size)
    {
        return _stream.Read(dataBuffer, 0, (int)size);
    }
}
