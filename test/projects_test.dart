import 'dart:io';

import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/project.dart';
import 'package:editor_23/screens/projects_screen.dart';
import 'package:editor_23/services/project_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late Directory dir;

  setUpAll(() async {
    dir = await Directory.systemTemp.createTemp('projects-test');
  });

  Project projectFor(String sourcePath, {String status = 'planned'}) {
    return Project(
      id: 'p1',
      name: 'clip.mp4',
      sourcePath: sourcePath,
      fileName: 'clip.mp4',
      instruction: 'keep the first 5 seconds',
      style: EditStyle.cinematic,
      status: status,
      plan: <String, dynamic>{
        'operations': <dynamic>[
          <String, dynamic>{'op': 'trim', 'start': 0.0, 'end': 5.0},
        ],
        'notes': '',
      },
      planModel: 'muse-spark-1.3',
      updatedAt: DateTime.now(),
    );
  }

  testWidgets('saved project reopens in the workspace', (
    WidgetTester tester,
  ) async {
    final File source = File('${dir.path}/clip.mp4')
      ..writeAsBytesSync(<int>[0, 1, 2, 3]);
    final ProjectStore store = ProjectStore.memory([projectFor(source.path)]);

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectsScreen(onCreateVideo: () {}, store: store),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('clip.mp4'), findsOneWidget);
    expect(find.textContaining('Planned'), findsOneWidget);

    await tester.tap(find.text('clip.mp4'));
    await tester.pumpAndSettle();

    expect(find.text('Workspace'), findsOneWidget);
    expect(find.text('keep the first 5 seconds'), findsOneWidget);
    expect(find.text('Trim'), findsOneWidget);
  });

  testWidgets('missing source explains instead of opening', (
    WidgetTester tester,
  ) async {
    final ProjectStore store = ProjectStore.memory([
      projectFor('${dir.path}/gone.mp4', status: 'draft'),
    ]);

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectsScreen(onCreateVideo: () {}, store: store),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('clip.mp4'));
    await tester.pump();

    expect(find.text('Workspace'), findsNothing);
    expect(find.textContaining('Source video is missing'), findsOneWidget);
  });
}
