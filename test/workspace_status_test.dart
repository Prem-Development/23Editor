import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/status_screen.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:editor_23/services/backend_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path_provider_platform_interface/path_provider_platform_interface.dart';
import 'package:plugin_platform_interface/plugin_platform_interface.dart';

class FakePathProviderPlatform extends PathProviderPlatform
    with MockPlatformInterfaceMixin {
  FakePathProviderPlatform(this.tempPath);

  final String tempPath;

  @override
  Future<String?> getTemporaryPath() async => tempPath;

  @override
  Future<String?> getApplicationDocumentsPath() async => tempPath;
}

BackendException _timeout() =>
    BackendException('Backend connection timed out.', null, const {}, true);

/// Fake whose cover/reference calls fail while the probe answers.
class TimeoutClient extends BackendClient {
  TimeoutClient({required this.probeAnswer, this.coverStatus = 0});

  final bool probeAnswer;

  /// HTTP status the cover route fails with (0 = timeout instead).
  final int coverStatus;

  @override
  Future<bool> probeReachable({
    Duration timeout = const Duration(seconds: 8),
  }) async {
    return probeAnswer;
  }

  @override
  Future<Map<String, dynamic>> getCapabilities() async {
    return <String, dynamic>{
      'sfx_assets': <String>['pop'],
    };
  }

  @override
  Future<Map<String, dynamic>> uploadFile(
    String path, {
    void Function(int sent, int total)? onProgress,
    String label = 'source',
  }) async {
    return <String, dynamic>{
      'file_id': 'f1',
      'label': label,
      'media': <String, dynamic>{
        'duration': 13.0,
        'width': 640,
        'height': 360,
        'has_audio': true,
      },
    };
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String route,
    Map<String, dynamic> body, {
    Duration timeout = const Duration(seconds: 120),
  }) async {
    if (route == '/api/cover') {
      if (coverStatus != 0) {
        throw BackendException('Cover render failed.', coverStatus);
      }
      throw _timeout();
    }
    if (route == '/api/reference/analyze') {
      throw _timeout();
    }
    throw BackendException('Unexpected route $route', 404);
  }

  @override
  Future<Map<String, dynamic>> getJson(String route) async {
    return <String, dynamic>{};
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String route) async {
    return <String, dynamic>{};
  }

  @override
  Future<void> downloadToFile(String route, String destPath) async {}
}

class StatusClient extends BackendClient {
  StatusClient(this.payload);

  final Map<String, dynamic> payload;

  @override
  Future<Map<String, dynamic>> getStatus() async => payload;
}

Finder _key(String name) => find.byKey(ValueKey<String>(name));

Map<String, dynamic> _statusPayload({
  bool key = true,
  String access = 'verified',
  bool ffmpeg = true,
}) {
  return <String, dynamic>{
    'service': '23editor-backend',
    'server_build': '9.9.9-test',
    'api_key_present': key,
    'api_access': access,
    'ffmpeg': <String, dynamic>{
      'present': ffmpeg,
      'version': ffmpeg ? 'ffmpeg version 9.0-test' : '',
    },
    'integrations': <Map<String, dynamic>>[
      <String, dynamic>{
        'id': 'spark_plan',
        'status': 'verified',
        'detail': 'Muse Spark editing plans.',
        'verified_at': '2026-11-03T10:00:00',
      },
      <String, dynamic>{
        'id': 'voice_transcribe',
        'status': 'provider_error',
        'detail':
            'Voice transcription. Latest attempt failed at '
            '2026-11-03T11:00:00: upstream 503.',
        'verified_at': '2026-11-02T09:00:00',
      },
      <String, dynamic>{
        'id': 'sam_track',
        'status': 'implemented_but_untested',
        'detail': 'Subject tracking.',
        'verified_at': null,
      },
      <String, dynamic>{
        'id': 'wonder_plan',
        'status': 'planned',
        'detail': 'Escalation path only.',
        'verified_at': null,
      },
    ],
  };
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    final Directory dir = await Directory.systemTemp.createTemp(
      'ws-status-test',
    );
    PathProviderPlatform.instance = FakePathProviderPlatform(dir.path);
  });

  Future<void> pumpWorkspace(WidgetTester tester, TimeoutClient client) async {
    await tester.pumpWidget(
      MaterialApp(
        home: WorkspaceScreen(
          video: const VideoSelection('clip.mp4'),
          setup: const EditSetup(instruction: 'keep the first 5 seconds'),
          onChanged: (_) {},
          client: client,
          onShare: (_) async {},
          pickReferenceVideo: () async => 'ref.mp4',
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  Future<void> makeCover(WidgetTester tester) async {
    await tester.ensureVisible(_key('coverTitleField'));
    await tester.pumpAndSettle();
    await tester.enterText(_key('coverTitleField'), 'My day');
    await tester.pump();
    await tester.ensureVisible(_key('makeCoverButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('makeCoverButton'));
    await tester.pumpAndSettle();
  }

  testWidgets('status screen shows connection facts and provider errors', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(home: StatusScreen(client: StatusClient(_statusPayload()))),
    );
    await tester.pumpAndSettle();

    expect(find.text('Connection'), findsOneWidget);
    expect(find.textContaining('Backend: reachable'), findsOneWidget);
    expect(find.textContaining('API key: configured'), findsOneWidget);
    expect(find.textContaining('Meta API access: Available'), findsOneWidget);
    expect(
      find.textContaining('FFmpeg: available (ffmpeg version 9.0-test)'),
      findsOneWidget,
    );
    expect(find.text('Verified'), findsOneWidget);
    expect(find.text('Provider error'), findsOneWidget);
    expect(find.textContaining('Latest attempt failed at'), findsOneWidget);
    // The last success stays visible beside the newer failure.
    expect(
      find.textContaining('Last live success: 2026-11-02T09:00:00'),
      findsOneWidget,
    );
    expect(find.text('Not yet tested'), findsOneWidget);
    expect(find.text('Planned'), findsOneWidget);
  });

  testWidgets('status screen explains a missing key and denied access', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: StatusScreen(
          client: StatusClient(
            _statusPayload(key: false, access: 'auth_error', ffmpeg: false),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('API key: missing'), findsOneWidget);
    expect(find.textContaining('META_API_KEY'), findsWidgets);
    expect(find.textContaining('Meta API access: Denied'), findsOneWidget);
    expect(find.textContaining('FFmpeg: missing'), findsOneWidget);
  });

  testWidgets('cover timeout with reachable backend stays a cover error', (
    WidgetTester tester,
  ) async {
    await pumpWorkspace(tester, TimeoutClient(probeAnswer: true));
    await makeCover(tester);

    expect(
      find.textContaining(
        'Cover timed out, but the backend is still '
        'reachable',
      ),
      findsOneWidget,
    );
    expect(find.text('Backend is not connected.'), findsNothing);
  });

  testWidgets('cover timeout with dead backend marks it disconnected', (
    WidgetTester tester,
  ) async {
    await pumpWorkspace(tester, TimeoutClient(probeAnswer: false));
    await makeCover(tester);

    expect(find.text('Backend is not connected.'), findsOneWidget);
  });

  testWidgets('cover HTTP failure never marks the backend disconnected', (
    WidgetTester tester,
  ) async {
    await pumpWorkspace(
      tester,
      TimeoutClient(probeAnswer: false, coverStatus: 503),
    );
    await makeCover(tester);

    expect(find.textContaining('Cover render failed.'), findsOneWidget);
    expect(find.text('Backend is not connected.'), findsNothing);
  });

  testWidgets('reference timeout with reachable backend stays local', (
    WidgetTester tester,
  ) async {
    await pumpWorkspace(tester, TimeoutClient(probeAnswer: true));

    final Finder pickButton = find.byKey(
      const ValueKey<String>('pickReferenceButton'),
    );
    await tester.ensureVisible(pickButton);
    await tester.pumpAndSettle();
    await tester.tap(pickButton);
    await tester.pumpAndSettle();

    expect(
      find.textContaining(
        'Reference analysis timed out, but the backend is still reachable',
      ),
      findsOneWidget,
    );
    expect(find.text('Backend is not connected.'), findsNothing);
  });
}
