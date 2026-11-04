import 'package:flutter/material.dart';

/// Full-width violet call-to-action button with a comfortable touch target.
class PrimaryButton extends StatelessWidget {
  final String label;
  final IconData? icon;
  final VoidCallback? onPressed;
  final bool expanded;

  const PrimaryButton({
    super.key,
    required this.label,
    this.icon,
    required this.onPressed,
    this.expanded = true,
  });

  @override
  Widget build(BuildContext context) {
    final ButtonStyle? style = Theme.of(context).elevatedButtonTheme.style;
    final Widget button = icon == null
        ? ElevatedButton(onPressed: onPressed, style: style, child: Text(label))
        : ElevatedButton.icon(
            onPressed: onPressed,
            style: style,
            icon: Icon(icon),
            label: Text(label),
          );
    if (!expanded) {
      return button;
    }
    return SizedBox(width: double.infinity, child: button);
  }
}
