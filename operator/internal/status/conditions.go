// Copyright 2026 ACC Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0

package status

import (
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// SetCondition updates or appends a condition in the conditions slice.
// It updates LastTransitionTime only when the Status changes.
func SetCondition(conditions *[]metav1.Condition, condType string, condStatus metav1.ConditionStatus, reason, message string) {
	now := metav1.NewTime(time.Now())

	for i, c := range *conditions {
		if c.Type == condType {
			if c.Status == condStatus {
				// Status unchanged — update message/reason but preserve transition time.
				(*conditions)[i].Reason = reason
				(*conditions)[i].Message = message
			} else {
				(*conditions)[i] = metav1.Condition{
					Type:               condType,
					Status:             condStatus,
					Reason:             reason,
					Message:            message,
					LastTransitionTime: now,
				}
			}
			return
		}
	}

	// New condition.
	*conditions = append(*conditions, metav1.Condition{
		Type:               condType,
		Status:             condStatus,
		Reason:             reason,
		Message:            message,
		LastTransitionTime: now,
	})
}

// GetCondition returns the condition with the given type, or nil.
func GetCondition(conditions []metav1.Condition, condType string) *metav1.Condition {
	for i := range conditions {
		if conditions[i].Type == condType {
			return &conditions[i]
		}
	}
	return nil
}

// IsConditionTrue returns true when the condition is present and True.
func IsConditionTrue(conditions []metav1.Condition, condType string) bool {
	c := GetCondition(conditions, condType)
	return c != nil && c.Status == metav1.ConditionTrue
}

// IsConditionFalse returns true when the condition is present and False.
func IsConditionFalse(conditions []metav1.Condition, condType string) bool {
	c := GetCondition(conditions, condType)
	return c != nil && c.Status == metav1.ConditionFalse
}

// RemoveCondition removes a condition by type if present.
func RemoveCondition(conditions *[]metav1.Condition, condType string) {
	filtered := (*conditions)[:0]
	for _, c := range *conditions {
		if c.Type != condType {
			filtered = append(filtered, c)
		}
	}
	*conditions = filtered
}
