import 'package:flutter/material.dart';

/// Editing styles the user can request. Null means no style preset.
enum EditStyle { productAd, cinematic, socialReel, tutorial }

/// Display metadata for [EditStyle].
extension EditStyleDisplay on EditStyle {
  String get label => switch (this) {
    EditStyle.productAd => 'Product Ad',
    EditStyle.cinematic => 'Cinematic',
    EditStyle.socialReel => 'Social Reel',
    EditStyle.tutorial => 'Tutorial',
  };

  /// What the backend actually applies for this preset. Keep in sync
  /// with the style effects in backend/server.py.
  String get effect => switch (this) {
    EditStyle.productAd => 'Adds one vivid color grade to the render.',
    EditStyle.cinematic => 'Adds a warm grade with 0.5s fade-in/out.',
    EditStyle.socialReel => 'Adds a 9:16 crop, only when aspect is Original.',
    EditStyle.tutorial =>
      'Adds no automatic effects — open Tutorial suggestions to add '
          'speech captions or step titles you approve.',
  };

  IconData get icon => switch (this) {
    EditStyle.productAd => Icons.shopping_bag_outlined,
    EditStyle.cinematic => Icons.movie_outlined,
    EditStyle.socialReel => Icons.smartphone_outlined,
    EditStyle.tutorial => Icons.school_outlined,
  };
}

/// Aspect ratios the user can request. Null means Original.
enum AspectRatioOption { original, vertical, wide, square }

/// Display metadata for [AspectRatioOption].
extension AspectRatioDisplay on AspectRatioOption {
  String get label => switch (this) {
    AspectRatioOption.original => 'Original',
    AspectRatioOption.vertical => '9:16',
    AspectRatioOption.wide => '16:9',
    AspectRatioOption.square => '1:1',
  };

  String get hint => switch (this) {
    AspectRatioOption.original => 'Keep the source shape',
    AspectRatioOption.vertical => 'Vertical story',
    AspectRatioOption.wide => 'Widescreen',
    AspectRatioOption.square => 'Square post',
  };

  IconData get icon => switch (this) {
    AspectRatioOption.original => Icons.crop_original_outlined,
    AspectRatioOption.vertical => Icons.crop_portrait_outlined,
    AspectRatioOption.wide => Icons.crop_landscape_outlined,
    AspectRatioOption.square => Icons.crop_square_outlined,
  };
}

/// Canonical backend values. Single source for what the app sends;
/// never hand-write these strings at call sites.
extension AspectRatioBackend on AspectRatioOption {
  String get backendValue => switch (this) {
    AspectRatioOption.original => 'original',
    AspectRatioOption.vertical => '9:16',
    AspectRatioOption.wide => '16:9',
    AspectRatioOption.square => '1:1',
  };
}

/// How a non-original aspect ratio fills the frame.
enum ResizeFit { pad, crop }

/// Display metadata for [ResizeFit].
extension ResizeFitDisplay on ResizeFit {
  String get label => switch (this) {
    ResizeFit.pad => 'Pad',
    ResizeFit.crop => 'Crop',
  };

  String get hint => switch (this) {
    ResizeFit.pad => 'Letterbox: whole picture, black bars',
    ResizeFit.crop => 'Fill: no bars, edges trimmed',
  };

  /// Exact backend enum value.
  String get backendValue => switch (this) {
    ResizeFit.pad => 'pad',
    ResizeFit.crop => 'crop',
  };

  IconData get icon => switch (this) {
    ResizeFit.pad => Icons.fit_screen_outlined,
    ResizeFit.crop => Icons.crop_outlined,
  };
}

/// Caption burn-in preference. Default means captions only appear when
/// the instruction asks for them; On/Off are explicit user choices that
/// override the planner.
enum CaptionChoice { auto, on, off }

/// Display metadata for [CaptionChoice].
extension CaptionChoiceDisplay on CaptionChoice {
  String get label => switch (this) {
    CaptionChoice.auto => 'Auto',
    CaptionChoice.on => 'On',
    CaptionChoice.off => 'Off',
  };

  String get hint => switch (this) {
    CaptionChoice.auto => 'Follows the edit brief and detected speech',
    CaptionChoice.on => 'Requests captions in the render',
    CaptionChoice.off => 'Disables captions',
  };

  /// Exact backend enum value; auto is the default when omitted.
  String get backendValue => switch (this) {
    CaptionChoice.auto => 'auto',
    CaptionChoice.on => 'on',
    CaptionChoice.off => 'off',
  };
}

/// The edit the user described: instruction plus requested style and
/// aspect ratio. Null style means no preset; null aspectRatio means
/// Original. Immutable; screens share updates through copies.
@immutable
class EditSetup {
  final String instruction;
  final EditStyle? style;
  final AspectRatioOption? aspectRatio;
  final ResizeFit fit;

  const EditSetup({
    this.instruction = '',
    this.style,
    this.aspectRatio,
    this.fit = ResizeFit.pad,
  });

  /// True when the user typed a non-blank instruction.
  bool get hasInstruction => instruction.trim().isNotEmpty;

  /// Human-readable style name, with an honest fallback when unset.
  String get styleLabel => style?.label ?? 'Not selected';

  /// Exact backend enum value; null aspect means Original.
  String get aspectRatioValue => aspectRatio?.backendValue ?? 'original';

  EditSetup copyWith({
    String? instruction,
    EditStyle? style,
    bool clearStyle = false,
    AspectRatioOption? aspectRatio,
    bool clearAspectRatio = false,
    ResizeFit? fit,
  }) {
    return EditSetup(
      instruction: instruction ?? this.instruction,
      style: clearStyle ? null : (style ?? this.style),
      aspectRatio: clearAspectRatio ? null : (aspectRatio ?? this.aspectRatio),
      fit: fit ?? this.fit,
    );
  }
}
