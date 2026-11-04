import 'package:editor_23/main.dart';
import 'package:editor_23/models/edit_setup.dart';
import 'package:editor_23/models/video_selection.dart';
import 'package:editor_23/screens/workspace_screen.dart';
import 'package:editor_23/widgets/option_chips.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

bool _isSelected(WidgetTester tester, String label) {
  final ChoiceChip chip = tester.widget<ChoiceChip>(
    find.widgetWithText(ChoiceChip, label),
  );
  return chip.selected;
}

/// Brings [label] into view via its own scrollable ancestor. Used instead
/// of bare scrollUntilVisible, whose implicit scrollable finder matches
/// both the screen scroll view and multiline text fields.
Future<void> _reveal(WidgetTester tester, String label) async {
  await tester.ensureVisible(find.text(label));
  await tester.pumpAndSettle();
}

class _ChipHarness extends StatefulWidget {
  const _ChipHarness();

  @override
  State<_ChipHarness> createState() => _ChipHarnessState();
}

class _ChipHarnessState extends State<_ChipHarness> {
  String? selected;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      home: Scaffold(
        body: OptionChips<String>(
          options: const <String>['A', 'B'],
          selected: selected,
          onSelected: (String? value) => setState(() => selected = value),
          labelOf: (String value) => value,
        ),
      ),
    );
  }
}

void main() {
  testWidgets('OptionChips selects, switches, and deselects', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const _ChipHarness());

    await tester.tap(find.text('A'));
    await tester.pump();
    expect(_isSelected(tester, 'A'), isTrue);
    expect(_isSelected(tester, 'B'), isFalse);

    await tester.tap(find.text('B'));
    await tester.pump();
    expect(_isSelected(tester, 'A'), isFalse);
    expect(_isSelected(tester, 'B'), isTrue);

    await tester.tap(find.text('B'));
    await tester.pump();
    expect(_isSelected(tester, 'A'), isFalse);
    expect(_isSelected(tester, 'B'), isFalse);
  });

  testWidgets('Create screen style chips toggle exclusively', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const MyApp());
    await tester.tap(find.widgetWithText(ElevatedButton, 'Create Video'));
    await tester.pumpAndSettle();

    await _reveal(tester, 'Cinematic');
    await tester.tap(find.text('Cinematic'));
    await tester.pump();
    expect(_isSelected(tester, 'Cinematic'), isTrue);

    await _reveal(tester, 'Tutorial');
    await tester.tap(find.text('Tutorial'));
    await tester.pump();
    expect(_isSelected(tester, 'Cinematic'), isFalse);
    expect(_isSelected(tester, 'Tutorial'), isTrue);

    await tester.tap(find.text('Tutorial'));
    await tester.pump();
    expect(_isSelected(tester, 'Tutorial'), isFalse);
  });

  testWidgets('Create screen aspect chips toggle with Original default', (
    WidgetTester tester,
  ) async {
    await tester.pumpWidget(const MyApp());
    await tester.tap(find.widgetWithText(ElevatedButton, 'Create Video'));
    await tester.pumpAndSettle();

    await _reveal(tester, '1:1');
    expect(_isSelected(tester, 'Original'), isFalse);
    expect(_isSelected(tester, '1:1'), isFalse);

    await tester.tap(find.text('1:1'));
    await tester.pump();
    expect(_isSelected(tester, '1:1'), isTrue);

    await tester.tap(find.text('1:1'));
    await tester.pump();
    expect(_isSelected(tester, '1:1'), isFalse);

    await _reveal(tester, '9:16');
    await tester.tap(find.text('9:16'));
    await tester.pump();
    expect(_isSelected(tester, '9:16'), isTrue);
    expect(_isSelected(tester, '1:1'), isFalse);
  });

  testWidgets('Workspace reflects setup and propagates chip edits', (
    WidgetTester tester,
  ) async {
    EditSetup? received;
    await tester.pumpWidget(
      MaterialApp(
        home: WorkspaceScreen(
          video: const VideoSelection('clip.mp4'),
          setup: const EditSetup(
            instruction: 'Make it punchy',
            style: EditStyle.cinematic,
            aspectRatio: AspectRatioOption.square,
          ),
          onChanged: (EditSetup setup) => received = setup,
        ),
      ),
    );

    expect(_isSelected(tester, 'Cinematic'), isTrue);
    expect(_isSelected(tester, '1:1'), isTrue);

    await _reveal(tester, 'Cinematic');
    await tester.tap(find.text('Cinematic'));
    await tester.pump();
    expect(received, isNotNull);
    expect(received!.style, isNull);
    expect(received!.aspectRatio, AspectRatioOption.square);

    await _reveal(tester, '9:16');
    await tester.tap(find.text('9:16'));
    await tester.pump();
    expect(received!.aspectRatio, AspectRatioOption.vertical);
    expect(received!.aspectRatioValue, '9:16');

    await tester.tap(find.text('9:16'));
    await tester.pump();
    expect(received!.aspectRatio, isNull);
    expect(received!.aspectRatioValue, 'original');

    await tester.pump(const Duration(seconds: 1));
  });
}
