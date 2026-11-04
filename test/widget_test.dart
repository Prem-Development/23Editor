import 'package:editor_23/main.dart';
import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('Home shows brand, tagline, and honest empty state', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const MyApp());

    expect(find.text('23Editor'), findsOneWidget);
    expect(find.text('Your footage. Your story.'), findsOneWidget);
    expect(find.text('Choose a clip. Tell us your vision.'), findsOneWidget);
    expect(find.widgetWithText(ElevatedButton, 'Create Video'), findsOneWidget);
    expect(find.text('No projects yet'), findsOneWidget);
  });

  testWidgets('Create Video opens the creation screen', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const MyApp());
    await tester.tap(find.widgetWithText(ElevatedButton, 'Create Video'));
    await tester.pumpAndSettle();

    expect(find.text('How would you like to edit this video?'), findsOneWidget);
    expect(find.text('Review Setup'), findsOneWidget);
    expect(find.text('Voice instruction (optional)'), findsOneWidget);
    expect(find.text('Record instruction'), findsOneWidget);
    for (final String label in <String>[
      'Product Ad',
      'Cinematic',
      'Social Reel',
      'Tutorial',
    ]) {
      expect(find.text(label), findsOneWidget);
    }
    for (final String label in <String>['Original', '9:16', '16:9', '1:1']) {
      expect(find.text(label), findsOneWidget);
    }
  });

  testWidgets('Review Setup requires a video first', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const MyApp());
    await tester.tap(find.widgetWithText(ElevatedButton, 'Create Video'));
    await tester.pumpAndSettle();

    await tester.ensureVisible(find.text('Review Setup'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Review Setup'));
    await tester.pump();

    expect(find.text('Select a video to continue.'), findsOneWidget);
    expect(find.text('Workspace'), findsNothing);
  });

  testWidgets('Projects tab shows an honest empty state', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const MyApp());
    await tester.tap(find.text('Projects'));
    await tester.pumpAndSettle();

    expect(find.text('No projects yet'), findsOneWidget);
    expect(find.widgetWithText(ElevatedButton, 'Create Video'), findsOneWidget);
  });

  testWidgets('Workspace offers plan, render, and export flow', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: WorkspaceScreen(
          video: const VideoSelection('clip.mp4'),
          setup: const EditSetup(instruction: 'Make it punchy'),
          onChanged: (_) {},
        ),
      ),
    );

    expect(find.text('Source video'), findsOneWidget);
    expect(find.text('AI editing'), findsOneWidget);
    expect(find.text('Make it punchy'), findsOneWidget);
    expect(find.text('Advanced: plan & retry'), findsOneWidget);

    await tester.ensureVisible(find.text('Show step-by-step controls'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Show step-by-step controls'));
    await tester.pumpAndSettle();

    expect(
      tester
          .widget<ElevatedButton>(
            find.widgetWithText(ElevatedButton, 'Generate plan'),
          )
          .onPressed,
      isNotNull,
    );
    expect(
      tester
          .widget<ElevatedButton>(
            find.widgetWithText(ElevatedButton, 'Render video'),
          )
          .onPressed,
      isNull,
    );
    expect(find.text('Generate a plan first.'), findsOneWidget);
    expect(
      tester
          .widget<OutlinedButton>(find.widgetWithText(OutlinedButton, 'Share'))
          .onPressed,
      isNull,
    );
    expect(find.text('Video cover & artwork (optional)'), findsOneWidget);

    // Let the preview attempt (and gracefully fail) without a platform player.
    await tester.pump(const Duration(seconds: 1));
  });
}
