import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

/// Error from the 23Editor backend or from reaching it.
///
/// [statusCode] is null when the backend itself could not be reached.
/// [timedOut] marks a timeout, which is ambiguous: the backend may be
/// down, or merely busy (e.g. waiting on the model provider). Callers
/// probe reachability before declaring the backend gone.
class BackendException implements Exception {
  final String message;
  final int? statusCode;

  /// Structured extras from the backend error envelope, e.g. a
  /// clarification `question`, `suggest_cover`, or a `diagnosis`.
  final Map<String, dynamic> details;

  final bool timedOut;

  BackendException(
    this.message, [
    this.statusCode,
    this.details = const {},
    this.timedOut = false,
  ]);

  bool get isConnectionError => statusCode == null;

  @override
  String toString() => message;
}

/// HTTP client for the 23Editor Python backend. Uses dart:io only.
///
/// The base URL defaults to the Android emulator's host loopback and can be
/// overridden at build time:
/// `flutter run --dart-define=BACKEND_URL=http://192.168.1.5:8080`
class BackendClient {
  static const String baseUrl = String.fromEnvironment(
    'BACKEND_URL',
    defaultValue: 'http://10.0.2.2:8080',
  );

  final HttpClient _http = HttpClient()
    ..connectionTimeout = const Duration(seconds: 10);

  void close() => _http.close(force: true);

  Future<Map<String, dynamic>> getStatus() async {
    final Map<String, dynamic> json = await _getJson('/status');
    return json;
  }

  /// Fast reachability probe: true when /status answers in time.
  /// Used to tell a busy backend (feature timeout) from a dead one.
  /// Never throws.
  Future<bool> probeReachable({
    Duration timeout = const Duration(seconds: 8),
  }) async {
    try {
      await _getJson('/status', timeout: timeout);
      return true;
    } on BackendException {
      return false;
    }
  }

  Future<Map<String, dynamic>> getCapabilities() async {
    return _getJson('/api/capabilities');
  }

  /// Uploads a media file without holding it fully in memory.
  /// [onProgress] reports real uploaded/total bytes. [label] marks the
  /// upload as `source` footage, a `reference` style video, or an
  /// `asset` (audio/image extra).
  Future<Map<String, dynamic>> uploadFile(
    String path, {
    void Function(int sent, int total)? onProgress,
    String label = 'source',
  }) async {
    final File file = File(path);
    final int total;
    try {
      total = await file.length();
    } on FileSystemException {
      throw BackendException('Could not read the recording file.');
    }
    if (total <= 0) {
      throw BackendException('Recording is empty — please record again.');
    }
    final String fileName = path
        .split(RegExp(r'[/\\]'))
        .last
        .replaceAll(RegExp(r'["\r\n]'), '_');
    final String boundary =
        '----dart${Random().nextInt(1 << 32).toRadixString(16)}';
    final List<int> header = utf8.encode(
      '--$boundary\r\n'
      'Content-Disposition: form-data; name="label"\r\n\r\n'
      '$label\r\n'
      '--$boundary\r\n'
      'Content-Disposition: form-data; name="file"; filename="$fileName"\r\n'
      'Content-Type: application/octet-stream\r\n\r\n',
    );
    final List<int> footer = utf8.encode('\r\n--$boundary--\r\n');
    try {
      final HttpClientRequest request = await _http.postUrl(
        Uri.parse('$baseUrl/api/upload'),
      );
      request.headers.set(
        HttpHeaders.contentTypeHeader,
        'multipart/form-data; boundary=$boundary',
      );
      request.contentLength = header.length + total + footer.length;
      request.add(header);
      int sent = 0;
      await for (final List<int> chunk in file.openRead()) {
        request.add(chunk);
        sent += chunk.length;
        onProgress?.call(sent, total);
      }
      request.add(footer);
      return await _readJson(request);
    } on BackendException {
      rethrow;
    } on SocketException {
      throw BackendException(
        'Backend is not reachable at $baseUrl. Start it with '
        "'python server.py' (see backend/README.md).",
      );
    } on TimeoutException {
      throw BackendException(
        'Backend connection timed out.',
        null,
        const {},
        true,
      );
    }
  }

  Future<Map<String, dynamic>> postJson(
    String route,
    Map<String, dynamic> body, {
    Duration timeout = const Duration(seconds: 120),
  }) async {
    try {
      final HttpClientRequest request = await _http
          .postUrl(Uri.parse('$baseUrl$route'))
          .timeout(const Duration(seconds: 15));
      request.headers.contentType = ContentType.json;
      // Byte length, not char length: transcripts may hold multibyte text.
      final List<int> bodyBytes = utf8.encode(jsonEncode(body));
      request.contentLength = bodyBytes.length;
      request.add(bodyBytes);
      return await _readJson(request, timeout: timeout);
    } on BackendException {
      rethrow;
    } on SocketException {
      throw BackendException(
        'Backend is not reachable at $baseUrl. Start it with '
        "'python server.py' (see backend/README.md).",
      );
    } on TimeoutException {
      throw BackendException(
        'Backend connection timed out.',
        null,
        const {},
        true,
      );
    }
  }

  /// Downloads a render/asset to [destPath] as a stream.
  Future<void> downloadToFile(String route, String destPath) async {
    try {
      final HttpClientRequest request = await _http
          .getUrl(Uri.parse('$baseUrl$route'))
          .timeout(const Duration(seconds: 15));
      final HttpClientResponse response = await request.close();
      if (response.statusCode != 200) {
        final String text = await response.transform(utf8.decoder).join();
        throw BackendException(
          _errorMessage(text, response.statusCode),
          response.statusCode,
        );
      }
      final IOSink sink = File(destPath).openWrite();
      try {
        await response.pipe(sink);
      } finally {
        await sink.close();
      }
    } on BackendException {
      rethrow;
    } on SocketException {
      throw BackendException(
        'Backend is not reachable at $baseUrl. Start it with '
        "'python server.py' (see backend/README.md).",
      );
    }
  }

  /// GET that returns decoded JSON (analysis polling, downloads aside).
  Future<Map<String, dynamic>> getJson(String route) {
    return _getJson(route);
  }

  /// DELETE that returns decoded JSON (analysis cancel).
  Future<Map<String, dynamic>> deleteJson(String route) async {
    try {
      final HttpClientRequest request = await _http
          .deleteUrl(Uri.parse('$baseUrl$route'))
          .timeout(const Duration(seconds: 15));
      return await _readJson(request);
    } on BackendException {
      rethrow;
    } on SocketException {
      throw BackendException(
        'Backend is not reachable at $baseUrl. Start it with '
        "'python server.py' (see backend/README.md).",
      );
    } on TimeoutException {
      throw BackendException(
        'Backend connection timed out.',
        null,
        const {},
        true,
      );
    }
  }

  Future<Map<String, dynamic>> _getJson(
    String route, {
    Duration timeout = const Duration(seconds: 120),
  }) async {
    try {
      final HttpClientRequest request = await _http
          .getUrl(Uri.parse('$baseUrl$route'))
          .timeout(const Duration(seconds: 15));
      return await _readJson(request, timeout: timeout);
    } on BackendException {
      rethrow;
    } on SocketException {
      throw BackendException(
        'Backend is not reachable at $baseUrl. Start it with '
        "'python server.py' (see backend/README.md).",
      );
    } on TimeoutException {
      throw BackendException(
        'Backend connection timed out.',
        null,
        const {},
        true,
      );
    }
  }

  Future<Map<String, dynamic>> _readJson(
    HttpClientRequest request, {
    Duration timeout = const Duration(seconds: 120),
  }) async {
    final HttpClientResponse response = await request.close().timeout(timeout);
    final String text = await response.transform(utf8.decoder).join();
    if (response.statusCode != 200 && response.statusCode != 202) {
      final Map<String, dynamic> envelope = _errorEnvelope(text);
      throw BackendException(
        _errorMessage(text, response.statusCode),
        response.statusCode,
        envelope,
      );
    }
    try {
      final Object? decoded = jsonDecode(text);
      if (decoded is! Map<String, dynamic>) {
        throw BackendException('Backend returned an unexpected response.');
      }
      return decoded;
    } on FormatException {
      throw BackendException('Backend returned an unexpected response.');
    }
  }

  Map<String, dynamic> _errorEnvelope(String text) {
    try {
      final Object? decoded = jsonDecode(text);
      if (decoded is Map<String, dynamic>) {
        return Map<String, dynamic>.from(decoded)..remove('error');
      }
    } on FormatException {
      // No structured extras available.
    }
    return const <String, dynamic>{};
  }

  String _errorMessage(String text, int status) {
    try {
      final Object? decoded = jsonDecode(text);
      if (decoded is Map<String, dynamic>) {
        final Object? error = decoded['error'];
        if (error is String && error.isNotEmpty) {
          return error;
        }
      }
    } on FormatException {
      // Fall through to the generic message below.
    }
    return 'Backend request failed (HTTP $status).';
  }
}
