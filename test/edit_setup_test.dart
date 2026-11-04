import 'package:editor_23/models/edit_setup.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('EditSetup has honest empty defaults', () {
    const EditSetup setup = EditSetup();
    expect(setup.instruction, isEmpty);
    expect(setup.hasInstruction, isFalse);
    expect(setup.style, isNull);
    expect(setup.styleLabel, 'Not selected');
    expect(setup.aspectRatio, isNull);
    expect(setup.aspectRatioValue, 'original');
  });

  test('hasInstruction ignores blank text', () {
    expect(const EditSetup(instruction: '   ').hasInstruction, isFalse);
    expect(
      const EditSetup(instruction: 'Trim the intro').hasInstruction,
      isTrue,
    );
  });

  test('copyWith updates only the given fields', () {
    const EditSetup setup = EditSetup(
      instruction: 'Make it punchy',
      style: EditStyle.cinematic,
      aspectRatio: AspectRatioOption.wide,
    );
    final EditSetup updated = setup.copyWith(
      aspectRatio: AspectRatioOption.vertical,
    );
    expect(updated.instruction, 'Make it punchy');
    expect(updated.style, EditStyle.cinematic);
    expect(updated.aspectRatio, AspectRatioOption.vertical);
    expect(setup.aspectRatio, AspectRatioOption.wide);
  });

  test('copyWith can clear the style', () {
    const EditSetup setup = EditSetup(style: EditStyle.tutorial);
    expect(setup.copyWith(clearStyle: true).style, isNull);
  });

  test('copyWith can clear the aspect ratio', () {
    const EditSetup setup = EditSetup(aspectRatio: AspectRatioOption.square);
    final EditSetup cleared = setup.copyWith(clearAspectRatio: true);
    expect(cleared.aspectRatio, isNull);
    expect(cleared.aspectRatioValue, 'original');
  });

  test('aspectRatioValue matches the backend enum exactly', () {
    expect(const EditSetup().aspectRatioValue, 'original');
    expect(
      const EditSetup(aspectRatio: AspectRatioOption.original).aspectRatioValue,
      'original',
    );
    expect(
      const EditSetup(aspectRatio: AspectRatioOption.vertical).aspectRatioValue,
      '9:16',
    );
    expect(
      const EditSetup(aspectRatio: AspectRatioOption.wide).aspectRatioValue,
      '16:9',
    );
    expect(
      const EditSetup(aspectRatio: AspectRatioOption.square).aspectRatioValue,
      '1:1',
    );
  });

  test('every style and aspect ratio has a label', () {
    for (final EditStyle style in EditStyle.values) {
      expect(style.label, isNotEmpty);
    }
    expect(EditStyle.values.map((EditStyle e) => e.label), <String>[
      'Product Ad',
      'Cinematic',
      'Social Reel',
      'Tutorial',
    ]);
    expect(
      AspectRatioOption.values.map((AspectRatioOption e) => e.label),
      <String>['Original', '9:16', '16:9', '1:1'],
    );
  });
}
