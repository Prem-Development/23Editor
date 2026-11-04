import 'package:flutter/material.dart';

/// Single-select chip group used for styles and aspect ratios.
/// Tapping the active option deselects it (reports null); tapping another
/// option switches the selection to it.
class OptionChips<T> extends StatelessWidget {
  final List<T> options;
  final T? selected;
  final ValueChanged<T?> onSelected;
  final String Function(T value) labelOf;
  final IconData Function(T value)? iconOf;

  const OptionChips({
    super.key,
    required this.options,
    required this.selected,
    required this.onSelected,
    required this.labelOf,
    this.iconOf,
  });

  @override
  Widget build(BuildContext context) {
    final IconData Function(T value)? iconOf = this.iconOf;
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: <Widget>[
        for (final T option in options)
          ChoiceChip(
            label: Text(labelOf(option)),
            avatar: iconOf == null ? null : Icon(iconOf(option), size: 18),
            selected: option == selected,
            showCheckmark: false,
            onSelected: (_) => onSelected(option == selected ? null : option),
          ),
      ],
    );
  }
}
