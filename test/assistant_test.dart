import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:editor_23/services/backend_client.dart';
import 'package:editor_23/widgets/brief_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path_provider_platform_interface/path_provider_platform_interface.dart';
import 'package:plugin_platform_interface/plugin_platform_interface.dart';

Map<String, dynamic> _analysisResult() => <String, dynamic>{
  'file_id': 'f1',
  'duration': 13.7,
  'width': 1080,
  'height': 1920,
  'orientation': 'portrait',
  'has_audio': true,
  'audio': {'class': 'non_silent'},
  'speech': {'present': true, 'words': 40, 'approximate': true},
  'cuts': [1.0, 5.0],
  'frames': [
    {'asset_id': 'a1', 't': 2.0, 'url': '/api/files/a1'},
  ],
  'frame_budget_note': 'Spot checks only.',
  'content_hint': {'guess': 'talking', 'confidence': 'low'},
  'timings': <String, int>{
    'audio_ms': 120,
    'pauses_ms': 340,
    'transcribe_ms': 2100,
    'cuts_ms': 410,
    'frames_ms': 800,
  },
};

class AssistantClient extends BackendClient {
  AssistantClient({
    this.briefResponse,
    this.thumbnailResponse,
    this.thumbnailError,
    this.transcribeResponse,
    this.transcribeError,
    this.referenceStyleResponse,
  });

  final Map<String, dynamic>? briefResponse;
  final Map<String, dynamic>? thumbnailResponse;
  final BackendException? thumbnailError;
  final Map<String, dynamic>? transcribeResponse;
  final BackendException? transcribeError;
  final Map<String, dynamic>? referenceStyleResponse;

  Map<String, dynamic>? planBody;
  Map<String, dynamic>? briefBody;
  String? lastUploadLabel;

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
    lastUploadLabel = label;
    return <String, dynamic>{'file_id': 'f1', 'label': label};
  }

  @override
  Future<Map<String, dynamic>> postJson(
    String route,
    Map<String, dynamic> body, {
    Duration timeout = const Duration(seconds: 120),
  }) async {
    switch (route) {
      case '/api/analyze/start':
        return <String, dynamic>{
          'job_id': 'j1',
          'state': 'done',
          'stage': 'Reused the cached analysis for this footage.',
          'result': _analysisResult(),
        };
      case '/api/brief':
        briefBody = body;
        return briefResponse ??
            <String, dynamic>{
              'brief': 'Keep the first 5 seconds and add captions.',
              'explains': <String>['Trims the start', 'Burns captions'],
              'language': 'en',
              'goal': 'captions',
            };
      case '/api/reference/analyze':
        if (referenceStyleResponse != null) {
          return referenceStyleResponse!;
        }
        return <String, dynamic>{
          'style': <String, dynamic>{
            'file_id': 'f2',
            'aspect': '9:16',
            'width': 1080,
            'height': 1920,
            'duration': 12.0,
            'shots': 6,
            'avg_shot_len': 2.0,
            'cuts': [2.0],
            'fades_to_black': 1,
            'has_audio': true,
            'transferable': <String>[
              'Framing: match the 9:16 aspect (optional).',
              'Pacing: 6 shots, about 2.0s each.',
            ],
            'unsupported': <String>['Caption layout is not detected.'],
          },
        };
      case '/api/cover':
        return <String, dynamic>{'image_id': 'c1', 'url': '/api/files/c1'};
      case '/api/thumbnail':
        if (thumbnailError != null) {
          throw thumbnailError!;
        }
        return thumbnailResponse ??
            <String, dynamic>{'image_id': 't1', 'url': '/api/files/t1'};
      case '/api/transcribe':
        if (transcribeError != null) {
          throw transcribeError!;
        }
        return transcribeResponse ??
            <String, dynamic>{
              'text': 'hello',
              'approximate': true,
              'duration': 13.7,
              'turns': <Map<String, dynamic>>[
                <String, dynamic>{
                  'text': 'hello there friend',
                  'raw': <String, dynamic>{},
                },
              ],
              'diagnosis': {'class': 'recognized'},
            };
      case '/api/plan':
        planBody = body;
        return <String, dynamic>{
          'plan': <String, dynamic>{
            'operations': <Map<String, dynamic>>[
              <String, dynamic>{'op': 'trim', 'start': 0.0, 'end': 5.0},
            ],
            'notes': '',
          },
          'warnings': <String>[],
          'pending': <String>[],
          'model': 'muse-spark-1.3',
        };
      case '/api/render':
        return <String, dynamic>{
          'render_id': 'r1',
          'url': '/api/files/r1',
          'steps_used': <String>['ffmpeg_render'],
          'warnings': <String>[],
          'adjustments': <String>[],
          'plan': body['plan'],
          'output': <String, dynamic>{'duration': 5.0, 'has_audio': true},
        };
    }
    throw BackendException('Unexpected route $route', 404);
  }

  @override
  Future<Map<String, dynamic>> getJson(String route) async {
    return <String, dynamic>{'job_id': 'j1', 'state': 'done'};
  }

  @override
  Future<Map<String, dynamic>> deleteJson(String route) async {
    return <String, dynamic>{'cancelled': true};
  }

  @override
  Future<void> downloadToFile(String route, String destPath) async {}
}

class FakePathProviderPlatform extends PathProviderPlatform
    with MockPlatformInterfaceMixin {
  FakePathProviderPlatform(this.tempPath);

  final String tempPath;

  @override
  Future<String?> getTemporaryPath() async => tempPath;

  @override
  Future<String?> getApplicationDocumentsPath() async => tempPath;
}

Finder _key(String name) => find.byKey(ValueKey<String>(name));

Future<void> _pump(
  WidgetTester tester, {
  required AssistantClient client,
  String instruction = 'keep the first 5 seconds',
  Future<String?> Function()? pickReferenceVideo,
  Future<void> Function(String path)? onSaveThumbnail,
  Future<void> Function(String path)? onShareThumbnail,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: WorkspaceScreen(
        video: const VideoSelection('clip.mp4'),
        setup: EditSetup(instruction: instruction),
        onChanged: (_) {},
        client: client,
        onShare: (_) async {},
        pickReferenceVideo: pickReferenceVideo,
        onSaveThumbnail: onSaveThumbnail,
        onShareThumbnail: onShareThumbnail,
      ),
    ),
  );
  await tester.pumpAndSettle();
}

Future<void> _expandAdvanced(WidgetTester tester) async {
  await tester.ensureVisible(find.text('Show step-by-step controls'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Show step-by-step controls'));
  await tester.pumpAndSettle();
}

Future<void> _plan(WidgetTester tester) async {
  await tester.ensureVisible(_key('generatePlanButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('generatePlanButton'));
  await tester.pumpAndSettle();
}

Future<void> _analyze(WidgetTester tester) async {
  expect(_key('analyzeButton'), findsOneWidget);
  await tester.ensureVisible(_key('analyzeButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('analyzeButton'));
  await tester.pumpAndSettle();
}

/// Scrolls the open BriefSheet to "Use this edit", taps it, and waits
/// for the sheet to dismiss. The sheet is taller than small viewports
/// once the brief appears, so the button must be scrolled into the
/// sheet's own scrollable before it can receive the tap.
Future<void> _tapUseBrief(WidgetTester tester) async {
  final Finder useButton = _key('useBriefButton');
  expect(useButton, findsOneWidget);
  await tester.ensureVisible(useButton);
  await tester.pumpAndSettle();
  expect(useButton.hitTestable(), findsOneWidget);
  await tester.tap(useButton);
  await tester.pumpAndSettle();
  expect(find.byType(BriefSheet), findsNothing);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    final Directory dir = await Directory.systemTemp.createTemp(
      'ws-assistant-test',
    );
    PathProviderPlatform.instance = FakePathProviderPlatform(dir.path);
  });

  testWidgets('analysis shows findings and reaches the plan', (
    WidgetTester tester,
  ) async {
    final AssistantClient client = AssistantClient();
    await _pump(tester, client: client);
    await _analyze(tester);

    expect(find.text('What I found in your video'), findsOneWidget);
    expect(find.textContaining('40 words'), findsOneWidget);
    expect(_key('suggestion_captions'), findsOneWidget);
    expect(_key('suggestion_tutorial'), findsOneWidget);
    expect(
      find.textContaining('Reused the cached analysis in'),
      findsOneWidget,
    );
    expect(
      find.textContaining('Server: speech 2.1s • cuts 0.4s • frames 0.8s'),
      findsOneWidget,
    );

    // The suggestion chip opens the brief flow with the tutorial goal.
    await tester.ensureVisible(_key('suggestion_tutorial'));
    await tester.pumpAndSettle();
    await tester.tap(_key('suggestion_tutorial'));
    await tester.pumpAndSettle();
    expect(
      find.descendant(
        of: find.byType(BriefSheet),
        matching: _key('briefSheetTitle'),
      ),
      findsOneWidget,
    );
    await tester.tap(_key('writeBriefButton'));
    await tester.pumpAndSettle();
    expect(client.briefBody?['goal'], 'tutorial');
    await _tapUseBrief(tester);

    await _expandAdvanced(tester);
    await _plan(tester);
    expect(client.planBody?['analysis'], isNotNull);
    expect((client.planBody!['analysis'] as Map)['orientation'], 'portrait');
  });

  testWidgets('brief sheet writes and applies an edit', (
    WidgetTester tester,
  ) async {
    final AssistantClient client = AssistantClient();
    await _pump(tester, client: client);
    await _analyze(tester);

    await tester.ensureVisible(_key('suggestion_captions'));
    await tester.pumpAndSettle();
    await tester.tap(_key('suggestion_captions'));
    await tester.pumpAndSettle();
    expect(
      find.descendant(
        of: find.byType(BriefSheet),
        matching: _key('briefSheetTitle'),
      ),
      findsOneWidget,
    );

    await tester.tap(_key('writeBriefButton'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Keep the first 5 seconds'), findsOneWidget);
    expect(find.text('Trims the start'), findsOneWidget);

    await _tapUseBrief(tester);
    expect(
      tester.widget<TextField>(_key('instructionField')).controller?.text,
      'Keep the first 5 seconds and add captions.',
    );
    await _expandAdvanced(tester);
    await _plan(tester);
    expect(
      client.planBody?['instruction'],
      'Keep the first 5 seconds and add captions.',
    );
  });

  testWidgets('brief question is shown instead of a guessed brief', (
    WidgetTester tester,
  ) async {
    final AssistantClient client = AssistantClient(
      briefResponse: <String, dynamic>{
        'question': 'Do you mean minutes or seconds?',
      },
    );
    await _pump(tester, client: client);

    await tester.ensureVisible(_key('briefButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('briefButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('writeBriefButton'));
    await tester.pumpAndSettle();
    expect(find.text('Do you mean minutes or seconds?'), findsOneWidget);
    expect(find.text('Use this edit'), findsNothing);
  });

  testWidgets('reference style applies to user footage explicitly', (
    WidgetTester tester,
  ) async {
    final AssistantClient client = AssistantClient();
    await _pump(
      tester,
      client: client,
      pickReferenceVideo: () async => 'ref.mp4',
    );

    await tester.ensureVisible(_key('pickReferenceButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('pickReferenceButton'));
    await tester.pumpAndSettle();
    expect(client.lastUploadLabel, 'reference');
    expect(find.textContaining('9:16'), findsWidgets);
    expect(find.textContaining('Matched:'), findsNWidgets(2));
    expect(find.textContaining('Not matched:'), findsOneWidget);

    // Same source without the applied reference: default aspect and no
    // style note in the instruction.
    await _expandAdvanced(tester);
    await _plan(tester);
    expect(client.planBody?['aspect_ratio'], 'original');
    expect(
      (client.planBody?['instruction'] as String).contains('reference'),
      isFalse,
    );

    await tester.ensureVisible(_key('applyReferenceButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('applyReferenceButton'));
    await tester.pumpAndSettle();
    await _plan(tester);
    expect(client.planBody?['aspect_ratio'], '9:16');
    expect(
      (client.planBody?['instruction'] as String).contains('reference'),
      isTrue,
    );
  });

  testWidgets('reference with no evidence says so and keeps aspect', (
    WidgetTester tester,
  ) async {
    final AssistantClient client = AssistantClient(
      referenceStyleResponse: <String, dynamic>{
        'style': <String, dynamic>{
          'file_id': 'f2',
          'aspect': 'original',
          'duration': 12.0,
          'shots': 1,
          'avg_shot_len': 12.0,
          'cuts': <double>[],
          'fades_to_black': 0,
          'has_audio': false,
          'transferable': <String>[],
          'unsupported': <String>['Caption layout is not detected.'],
        },
      },
    );
    await _pump(
      tester,
      client: client,
      pickReferenceVideo: () async => 'ref.mp4',
    );

    await tester.ensureVisible(_key('pickReferenceButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('pickReferenceButton'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Matched:'), findsNothing);
    expect(
      find.textContaining('Little transferable style evidence'),
      findsOneWidget,
    );

    await tester.ensureVisible(_key('applyReferenceButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('applyReferenceButton'));
    await tester.pumpAndSettle();
    await _expandAdvanced(tester);
    await _plan(tester);
    // No framing evidence: the source aspect is untouched.
    expect(client.planBody?['aspect_ratio'], 'original');
  });

  testWidgets('cover composes from footage and saves', (
    WidgetTester tester,
  ) async {
    final AssistantClient client = AssistantClient();
    final List<String> saved = <String>[];
    await _pump(
      tester,
      client: client,
      onSaveThumbnail: (String path) async {
        saved.add(path);
      },
    );

    expect(_key('makeCoverButton'), findsOneWidget);
    await tester.ensureVisible(_key('coverTitleField'));
    await tester.pumpAndSettle();
    await tester.enterText(_key('coverTitleField'), 'My day');
    await tester.pump();
    await tester.ensureVisible(_key('makeCoverButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('makeCoverButton'));
    await tester.pumpAndSettle();

    expect(_key('saveCoverButton'), findsOneWidget);
    await tester.ensureVisible(_key('saveCoverButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('saveCoverButton'));
    await tester.pumpAndSettle();
    expect(saved, hasLength(1));
  });

  testWidgets('artwork clarification shows without generating', (
    WidgetTester tester,
  ) async {
    final AssistantClient client = AssistantClient(
      thumbnailError: BackendException(
        "What is 'Air rakumaan' — a person, a product, or something else?",
        422,
        {'question': 'What is it?'},
      ),
    );
    await _pump(tester, client: client);

    await tester.ensureVisible(find.text('Artwork'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Artwork'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generateThumbnailButton'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Air rakumaan'), findsOneWidget);
    expect(_key('saveThumbnailButton'), findsNothing);
  });

  testWidgets('policy refusal offers the frame cover instead', (
    WidgetTester tester,
  ) async {
    final AssistantClient client = AssistantClient(
      thumbnailError: BackendException(
        'The image service refused this request.',
        422,
        {'suggest_cover': true},
      ),
    );
    await _pump(tester, client: client);

    await tester.ensureVisible(find.text('Artwork'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Artwork'));
    await tester.pumpAndSettle();
    await tester.tap(_key('generateThumbnailButton'));
    await tester.pumpAndSettle();
    expect(find.textContaining('refused'), findsOneWidget);

    await tester.ensureVisible(_key('useCoverInsteadButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('useCoverInsteadButton'));
    await tester.pumpAndSettle();
    expect(_key('makeCoverButton'), findsOneWidget);
  });

  testWidgets('silence diagnosis offers a written title instead', (
    WidgetTester tester,
  ) async {
    final AssistantClient client = AssistantClient(
      transcribeError: BackendException(
        'This audio is silent, so there is nothing to transcribe.',
        422,
        {
          'diagnosis': {'class': 'silence', 'suggest_titles': true},
        },
      ),
    );
    await _pump(tester, client: client);

    await tester.ensureVisible(_key('transcribeButton'));
    await tester.pumpAndSettle();
    await tester.tap(_key('transcribeButton'));
    await tester.pumpAndSettle();
    expect(find.textContaining('no fabricated captions'), findsOneWidget);

    await tester.enterText(_key('titleInsteadField'), 'Sunrise');
    await tester.pump();
    await tester.tap(_key('addTitleInsteadButton'));
    await tester.pump();
    await _expandAdvanced(tester);
    await _plan(tester);
    expect(
      (client.planBody?['instruction'] as String).contains('Sunrise'),
      isTrue,
    );
  });

  testWidgets('sfx field shows the valid range for the edit', (
    WidgetTester tester,
  ) async {
    final AssistantClient client = AssistantClient();
    await _pump(tester, client: client);
    await _expandAdvanced(tester);
    await _plan(tester);

    await tester.ensureVisible(_key('sfxAtField'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Valid: 0–4.9s'), findsOneWidget);
  });

  testWidgets('empty instruction explains the disabled action', (
    WidgetTester tester,
  ) async {
    final AssistantClient client = AssistantClient();
    await _pump(tester, client: client, instruction: '');

    expect(find.textContaining('to enable Generate'), findsOneWidget);
    expect(find.text('Diagnostics'), findsOneWidget);
  });
}
