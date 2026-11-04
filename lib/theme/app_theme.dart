import 'package:flutter/material.dart';

/// Brand colors for 23Editor: dark charcoal surfaces with a violet accent.
abstract final class AppColors {
  static const Color charcoal = Color(0xFF14141C);
  static const Color surface = Color(0xFF1F1F2B);
  static const Color surfaceHigh = Color(0xFF2A2A38);
  static const Color violet = Color(0xFF8B5CF6);
  static const Color violetDeep = Color(0xFF6D28D9);
  static const Color violetSoft = Color(0xFFC4B0FF);
  static const Color ink = Color(0xFFF5F3FC);
  static const Color muted = Color(0xFFC6C0D8);
  static const Color outline = Color(0xFF3B3B4C);
  static const Color danger = Color(0xFFF87171);

  /// Restrained gradient, used sparingly for the logo tile and hero art.
  static const LinearGradient heroGradient = LinearGradient(
    colors: <Color>[violetDeep, violet],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );
}

/// Builds the dark, beginner-friendly theme used across the app.
ThemeData buildAppTheme() {
  const ColorScheme scheme = ColorScheme.dark(
    primary: AppColors.violet,
    onPrimary: Colors.white,
    secondary: AppColors.violetSoft,
    onSecondary: Color(0xFF1A1330),
    surface: AppColors.surface,
    onSurface: AppColors.ink,
    error: AppColors.danger,
    onError: Colors.white,
  );

  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: AppColors.charcoal,
    splashFactory: InkSparkle.splashFactory,
    iconTheme: const IconThemeData(color: AppColors.ink),
    appBarTheme: const AppBarTheme(
      backgroundColor: AppColors.charcoal,
      foregroundColor: AppColors.ink,
      elevation: 0,
      centerTitle: false,
      titleTextStyle: TextStyle(
        fontSize: 20,
        fontWeight: FontWeight.w700,
        color: AppColors.ink,
      ),
    ),
    cardTheme: CardThemeData(
      color: AppColors.surface,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: const BorderSide(color: AppColors.outline),
      ),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: AppColors.violet,
        foregroundColor: Colors.white,
        disabledBackgroundColor: AppColors.surfaceHigh,
        disabledForegroundColor: AppColors.muted,
        minimumSize: const Size(48, 52),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: AppColors.ink,
        minimumSize: const Size(48, 52),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        side: const BorderSide(color: AppColors.outline, width: 1.5),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: AppColors.violetSoft,
        minimumSize: const Size(48, 48),
        textStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AppColors.surfaceHigh,
      hintStyle: TextStyle(color: AppColors.muted.withValues(alpha: 0.7)),
      labelStyle: const TextStyle(color: AppColors.muted),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide.none,
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: BorderSide.none,
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: AppColors.violet, width: 1.5),
      ),
      errorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: AppColors.danger, width: 1.5),
      ),
    ),
    chipTheme: const ChipThemeData(
      backgroundColor: AppColors.surfaceHigh,
      disabledColor: AppColors.surfaceHigh,
      selectedColor: AppColors.violet,
      secondarySelectedColor: AppColors.violet,
      padding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      labelStyle: TextStyle(
        fontSize: 14,
        fontWeight: FontWeight.w600,
        color: AppColors.ink,
      ),
      secondaryLabelStyle: TextStyle(
        fontSize: 14,
        fontWeight: FontWeight.w600,
        color: Colors.white,
      ),
      brightness: Brightness.dark,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(12)),
      ),
    ),
    sliderTheme: SliderThemeData(
      activeTrackColor: AppColors.violet,
      inactiveTrackColor: AppColors.surfaceHigh,
      thumbColor: Colors.white,
      overlayColor: AppColors.violet.withValues(alpha: 0.2),
      valueIndicatorColor: AppColors.violetDeep,
      valueIndicatorTextStyle: const TextStyle(color: Colors.white),
    ),
    bottomNavigationBarTheme: const BottomNavigationBarThemeData(
      backgroundColor: AppColors.charcoal,
      selectedItemColor: AppColors.violetSoft,
      unselectedItemColor: AppColors.muted,
      showUnselectedLabels: true,
      selectedLabelStyle: TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
      unselectedLabelStyle: TextStyle(fontSize: 12),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: AppColors.surfaceHigh,
      contentTextStyle: const TextStyle(color: AppColors.ink, fontSize: 14),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      behavior: SnackBarBehavior.floating,
    ),
    dividerTheme: const DividerThemeData(
      color: AppColors.outline,
      thickness: 1,
      space: 24,
    ),
    textTheme: const TextTheme(
      headlineSmall: TextStyle(
        fontSize: 24,
        fontWeight: FontWeight.w700,
        color: AppColors.ink,
      ),
      titleLarge: TextStyle(
        fontSize: 20,
        fontWeight: FontWeight.w700,
        color: AppColors.ink,
      ),
      titleMedium: TextStyle(
        fontSize: 16,
        fontWeight: FontWeight.w600,
        color: AppColors.ink,
      ),
      titleSmall: TextStyle(
        fontSize: 14,
        fontWeight: FontWeight.w600,
        color: AppColors.ink,
      ),
      bodyLarge: TextStyle(fontSize: 16, color: AppColors.ink),
      bodyMedium: TextStyle(fontSize: 14, height: 1.5, color: AppColors.ink),
      bodySmall: TextStyle(fontSize: 13, height: 1.5, color: AppColors.muted),
    ),
  );
}
