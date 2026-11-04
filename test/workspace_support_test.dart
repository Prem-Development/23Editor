import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/project.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:editor_23/services/backend_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path_provider_platform_interface/path_provider_platform_interface.dart';
import 'package:plugin_platform_interface/plugin_platform_interface.dart';

class SupportClient extends BackendClient {
  SupportClient({this.supportSeconds = 8.0});

  final double supportSeconds;
  Map<String, dynamic>? lastRenderPlan;
  int supportUploads = 0;

  @override
  Future<Map<String, dynamic>> getCapabilities() async {
    return <String, dynamic>{
      'operations': <dynamic>[],
      'sfx_assets': <String>['pop'],
      'grade_presets': <String>['warm'],
      'banner_positions': <String>['bottom'],
      'tamil_font': <String, dynamic>{'available': true},
    };
  }

  @override
  Future<Map<String, dynamic>> uploadFile(
    String path, {
    void Function(int sent, int total)? onProgress,
    String label = 'source',
  }) async {
    if (label == 'asset' && path.endsWith('.png')) {
      return <String, dynamic>{
        'file_id': 'b' * 32,
        'label': label,
        'media': <String, dynamic>{'width': 800, 'height': 600},
      };
    }
    if (label == 'asset') {
      supportUploads++;
      return <String, dynamic>{
        'file_id': 'e' * 32,
        'label': label,
        'media': <String, dynamic>{
          'duration': supportSeconds,
          'width': 640,
          'height': 360,
          'has_audio': true,
        },
      };
    }
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
    Duration timeout = const Duration(seconds: 30),
  }) async {
    if (route == '/api/plan') {
      return <String, dynamic>{
        'plan': <String, dynamic>{
          'operations': <Map<String, dynamic>>[
            <String, dynamic>{'op': 'trim', 'start': 2.0, 'end': 10.0},
          ],
          'notes': '',
        },
        'warnings': <String>[],
        'pending': <String>[],
        'model': 'support-test-model',
      };
    }
    if (route == '/api/render') {
      lastRenderPlan = body['plan'] as Map<String, dynamic>?;
      return <String, dynamic>{
        'render_id': 'r1',
        'url': '/api/files/r1',
        'steps_used': <String>['ffmpeg_render'],
        'warnings': <String>[],
        'adjustments': <String>[],
        'plan': body['plan'],
        'output': <String, dynamic>{'duration': 8.0, 'has_audio': true},
      };
    }
    if (route == '/api/transcribe') {
      return <String, dynamic>{
        'text': 'garden tour transcript',
        'approximate': false,
        'duration': 13.0,
        'turns': <Map<String, dynamic>>[
          <String, dynamic>{
            'text': 'welcome to the garden tour',
            'raw': <String, dynamic>{'start': 3.0, 'end': 5.0},
          },
          <String, dynamic>{
            'text': 'now the garden tour continues inside',
            'raw': <String, dynamic>{'start': 6.0, 'end': 8.0},
          },
          <String, dynamic>{
            'text': 'garden tour outtakes at the start',
            'raw': <String, dynamic>{'start': 0.5, 'end': 1.5},
          },
        ],
      };
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

class FakePathProviderPlatform extends PathProviderPlatform
    with MockPlatformInterfaceMixin {
  FakePathProviderPlatform(this.tempDir);

  final Directory tempDir;

  @override
  Future<String?> getTemporaryPath() async => tempDir.path;

  @override
  Future<String?> getApplicationDocumentsPath() async => tempDir.path;

  @override
  Future<String?> getApplicationSupportPath() async => tempDir.path;
}

Finder _key(String name) => find.byKey(ValueKey<String>(name));

Future<void> _pump(
  WidgetTester tester, {
  required SupportClient client,
  List<String>? supportQueue,
  List<String>? imageQueue,
  Project? project,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: WorkspaceScreen(
        video: const VideoSelection('clip.mp4'),
        setup: const EditSetup(
          instruction: 'keep only the middle 8 seconds',
        ),
        onChanged: (_) {},
        client: client,
        onShare: (_) async {},
        pickImage: imageQueue == null
            ? null
            : () async => imageQueue.isNotEmpty
                  ? imageQueue.removeAt(0)
                  : null,
        pickSupport: supportQueue == null
            ? null
            : () async => supportQueue.isNotEmpty
                  ? supportQueue.removeAt(0)
                  : null,
        project: project,
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

Future<void> _tapPlan(WidgetTester tester) async {
  await tester.ensureVisible(_key('generatePlanButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('generatePlanButton'));
  await tester.pumpAndSettle();
}

Future<void> _tapRender(WidgetTester tester) async {
  await tester.ensureVisible(_key('renderVideoButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('renderVideoButton'));
  await tester.pumpAndSettle();
}

Future<void> _tapTranscribe(WidgetTester tester) async {
  await tester.ensureVisible(_key('transcribeButton'));
  await tester.pumpAndSettle();
  await tester.tap(_key('transcribeButton'));
  await tester.pumpAndSettle();
}

Future<void> _selectDropdown(
  WidgetTester tester,
  String fieldKey,
  String optionLabel,
) async {
  await tester.ensureVisible(_key(fieldKey));
  await tester.pumpAndSettle();
  await tester.tap(_key(fieldKey));
  await tester.pumpAndSettle();
  await tester.tap(find.text(optionLabel).last);
  await tester.pumpAndSettle();
}

Future<void> _enterField(
  WidgetTester tester,
  String fieldKey,
  String value,
) async {
  await tester.ensureVisible(_key(fieldKey));
  await tester.pumpAndSettle();
  await tester.enterText(_key(fieldKey), value);
  await tester.pump();
}

Future<void> _tapKey(WidgetTester tester, String name) async {
  await tester.ensureVisible(_key(name));
  await tester.pumpAndSettle();
  await tester.tap(_key(name));
  await tester.pumpAndSettle();
}

List<Map<String, dynamic>> _renderOps(SupportClient client) {
  final Map<String, dynamic>? plan = client.lastRenderPlan;
  if (plan == null) {
    return <Map<String, dynamic>>[];
  }
  return (plan['operations'] as List)
      .map((Object? op) => Map<String, dynamic>.from(op as Map))
      .toList();
}

void main() {
  late Directory tempDir;

  setUpAll(() async {
    tempDir = await Directory.systemTemp.createTemp('support_test_');
    PathProviderPlatform.instance = FakePathProviderPlatform(tempDir);
  });

  // Sync IO inside: test bodies run in fake async, where awaited
  // file writes never complete.
  Future<String> makeClip(String name) async {
    final File file = File('${tempDir.path}/$name');
    file.writeAsBytesSync(<int>[0, 1, 2, 3]);
    return file.path;
  }

  testWidgets('cutaway at edited time renders with sound choice', (
    WidgetTester tester,
  ) async {
    final SupportClient client = SupportClient();
    final String support = await makeClip('broll.mp4');
    await _pump(tester, client: client, supportQueue: <String>[support]);

    await _tapKey(tester, 'supportPickButton');
    expect(find.textContaining('broll.mp4 (Uploaded)'), findsOneWidget);

    await _enterField(tester, 'supportAtField', '4.0');
    await _selectDropdown(tester, 'supportAudioField', 'use');
    await _tapKey(tester, 'addSupportButton');
    // Once in the placements list, once in the "Edits to apply" summary.
    expect(
      find.textContaining('Cutaway (broll.mp4): 4.0s of the edit'),
      findsWidgets,
    );

    await _expandAdvanced(tester);
    await _tapPlan(tester);
    await _tapRender(tester);

    final Map<String, dynamic> op = _renderOps(client).firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'cutaway',
    );
    expect(op['file_id'], 'e' * 32);
    expect(op['at'], 4.0);
    expect(op['duration'], 2.0);
    expect(op['src_start'], 0.0);
    expect(op['audio'], 'use');
  });

  testWidgets('phrase with two kept lines lets the creator choose', (
    WidgetTester tester,
  ) async {
    final SupportClient client = SupportClient();
    final String support = await makeClip('broll.mp4');
    await _pump(tester, client: client, supportQueue: <String>[support]);
    await _tapKey(tester, 'supportPickButton');

    await _tapTranscribe(tester);
    await _expandAdvanced(tester);
    await _tapPlan(tester);

    await _enterField(tester, 'supportPhraseField', 'garden tour');
    await _tapKey(tester, 'supportPhraseFindButton');
    expect(
      find.textContaining('occurs 3 times'),
      findsOneWidget,
    );
    expect(find.textContaining('cut from the edit'), findsOneWidget);
    expect(find.textContaining('Clip 1 (clip.mp4)'), findsNWidgets(3));

    // Second kept line: source 6.0s, plan keeps 2..10, so edited 4.0s.
    await _tapKey(tester, 'supportCandidate_1');
    await _tapKey(tester, 'addSupportButton');
    expect(
      find.textContaining('at “garden tour”'),
      findsWidgets,
    );

    // The placement is a new render input: re-plan, then render.
    await _tapPlan(tester);
    await _tapRender(tester);
    final Map<String, dynamic> op = _renderOps(client).firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'cutaway',
    );
    expect(op['at'], closeTo(4.0, 1e-6));
  });

  testWidgets('phrase inside a cut region explains instead of placing', (
    WidgetTester tester,
  ) async {
    final SupportClient client = SupportClient();
    final String support = await makeClip('broll.mp4');
    await _pump(tester, client: client, supportQueue: <String>[support]);
    await _tapKey(tester, 'supportPickButton');

    await _tapTranscribe(tester);
    await _expandAdvanced(tester);
    await _tapPlan(tester);

    await _enterField(tester, 'supportPhraseField', 'garden tour');
    await _tapKey(tester, 'supportPhraseFindButton');
    // Third line sits at 0.5s, inside the removed 0..2s head.
    await _tapKey(tester, 'supportCandidate_2');
    await _tapKey(tester, 'addSupportButton');

    expect(find.textContaining('was cut from the edit'), findsOneWidget);
    expect(client.lastRenderPlan, isNull);
  });

  testWidgets('intro still shifts phrase placement to edited seconds', (
    WidgetTester tester,
  ) async {
    final SupportClient client = SupportClient();
    final String support = await makeClip('broll.mp4');
    final File image = File('${tempDir.path}/still.png');
    image.writeAsBytesSync(<int>[137, 80, 78, 71]);
    await _pump(
      tester,
      client: client,
      supportQueue: <String>[support],
      imageQueue: <String>[image.path],
    );

    // Attach a 2s intro still first.
    await _tapKey(tester, 'uploadImageButton');
    await _selectDropdown(tester, 'attachKindField', 'Intro still');
    await _enterField(tester, 'attachDurationField', '2.0');
    await _tapKey(tester, 'addAttachmentButton');
    expect(find.textContaining('2.0s intro image'), findsWidgets);

    await _tapKey(tester, 'supportPickButton');
    await _tapTranscribe(tester);
    await _expandAdvanced(tester);
    await _tapPlan(tester);

    await _enterField(tester, 'supportPhraseField', 'garden tour');
    await _tapKey(tester, 'supportPhraseFindButton');
    // First kept line: source 3.0s -> content 1.0s -> edited 3.0s.
    await _tapKey(tester, 'supportCandidate_0');
    await _tapKey(tester, 'addSupportButton');

    // The placement is a new render input: re-plan, then render.
    await _tapPlan(tester);
    await _tapRender(tester);
    final Map<String, dynamic> op = _renderOps(client).firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'cutaway',
    );
    expect(op['at'], closeTo(3.0, 1e-6));
  });

  testWidgets('picture-in-picture keeps position, size, and mix', (
    WidgetTester tester,
  ) async {
    final SupportClient client = SupportClient();
    final String support = await makeClip('broll.mp4');
    await _pump(tester, client: client, supportQueue: <String>[support]);
    await _tapKey(tester, 'supportPickButton');

    await _selectDropdown(tester, 'supportKindField', 'Picture-in-picture');
    await _selectDropdown(tester, 'supportPositionField', 'Bottom left');
    await _selectDropdown(tester, 'supportAudioField', 'mix');
    await _enterField(tester, 'supportAtField', '1.0');
    await _tapKey(tester, 'addSupportButton');
    expect(
      find.textContaining('Picture-in-picture (broll.mp4)'),
      findsWidgets,
    );

    await _expandAdvanced(tester);
    await _tapPlan(tester);
    await _tapRender(tester);

    final Map<String, dynamic> op = _renderOps(client).firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'pip',
    );
    expect(op['position'], 'bottom_left');
    expect(op['width_frac'], 0.25);
    expect(op['audio'], 'mix');
  });

  testWidgets('insert converts edited seconds past the intro', (
    WidgetTester tester,
  ) async {
    final SupportClient client = SupportClient();
    final String support = await makeClip('broll.mp4');
    final File image = File('${tempDir.path}/still.png');
    image.writeAsBytesSync(<int>[137, 80, 78, 71]);
    await _pump(
      tester,
      client: client,
      supportQueue: <String>[support],
      imageQueue: <String>[image.path],
    );

    await _tapKey(tester, 'uploadImageButton');
    await _selectDropdown(tester, 'attachKindField', 'Intro still');
    await _enterField(tester, 'attachDurationField', '2.0');
    await _tapKey(tester, 'addAttachmentButton');

    await _tapKey(tester, 'supportPickButton');
    await _selectDropdown(
      tester,
      'supportKindField',
      'Insert clip (extends the edit)',
    );
    await _selectDropdown(tester, 'supportAudioField', 'use');
    await _enterField(tester, 'supportAtField', '5.0');
    await _tapKey(tester, 'addSupportButton');

    await _expandAdvanced(tester);
    await _tapPlan(tester);
    await _tapRender(tester);

    final Map<String, dynamic> op = _renderOps(client).firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'insert',
    );
    // Edited 5.0s minus the 2s intro still = content 3.0s.
    expect(op['at'], closeTo(3.0, 1e-6));
    expect(op['audio'], 'use');
  });

  testWidgets('support segment past the clip length is rejected', (
    WidgetTester tester,
  ) async {
    final SupportClient client = SupportClient(supportSeconds: 8.0);
    final String support = await makeClip('broll.mp4');
    await _pump(tester, client: client, supportQueue: <String>[support]);
    await _tapKey(tester, 'supportPickButton');

    await _enterField(tester, 'supportAtField', '1.0');
    await _enterField(tester, 'supportSrcField', '7.0');
    await _tapKey(tester, 'addSupportButton');

    expect(find.textContaining('9.0'), findsOneWidget);
    expect(find.textContaining('8.0s supporting clip'), findsOneWidget);
  });

  testWidgets('reopened project keeps placements and renders them', (
    WidgetTester tester,
  ) async {
    final SupportClient client = SupportClient();
    final Project project = Project(
      id: 'p1',
      name: 'clip.mp4',
      sourcePath: 'clip.mp4',
      fileName: 'clip.mp4',
      updatedAt: DateTime(2026, 1, 1),
      support: <Map<String, dynamic>>[
        <String, dynamic>{
          'kind': 'cutaway',
          'fileId': 'e' * 32,
          'name': 'broll.mp4',
          'at': 4.0,
          'duration': 2.0,
          'srcStart': 1.0,
          'audio': 'mix',
        },
      ],
    );
    await _pump(tester, client: client, project: project);

    expect(
      find.textContaining('Cutaway (broll.mp4): 4.0s of the edit'),
      findsWidgets,
    );

    await _expandAdvanced(tester);
    await _tapPlan(tester);
    await _tapRender(tester);

    final Map<String, dynamic> op = _renderOps(client).firstWhere(
      (Map<String, dynamic> op) => op['op'] == 'cutaway',
    );
    expect(op['src_start'], 1.0);
    expect(op['audio'], 'mix');
  });

  testWidgets('removing a placement drops it from the render', (
    WidgetTester tester,
  ) async {
    final SupportClient client = SupportClient();
    final String support = await makeClip('broll.mp4');
    await _pump(tester, client: client, supportQueue: <String>[support]);
    await _tapKey(tester, 'supportPickButton');

    await _enterField(tester, 'supportAtField', '4.0');
    await _tapKey(tester, 'addSupportButton');
    await _tapKey(tester, 'removeSupport_0');
    expect(find.textContaining('Cutaway (broll.mp4)'), findsNothing);

    await _expandAdvanced(tester);
    await _tapPlan(tester);
    await _tapRender(tester);

    expect(
      _renderOps(client).where(
        (Map<String, dynamic> op) => op['op'] == 'cutaway',
      ),
      isEmpty,
    );
  });
}
