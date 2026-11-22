"""A rating method that ranks models by the goals they score and concede.

Each model starts from the average goal difference of its matches. The anchor is set to the
rating of the table, and every other model reads against it, 200 points for each goal of
difference per match. The deviation shrinks as the matches of a model grow in number.
"""

import math

SCALE = 200.0


class GoalRating:
    def fit(self, rows, anchor, options):
        gaps = {anchor: []}
        for home, away, score in rows:
            difference = score[0] - score[1]
            gaps.setdefault(home, []).append(difference)
            gaps.setdefault(away, []).append(-difference)
        mean = {key: sum(gaps[key]) / len(gaps[key]) if gaps[key] else 0.0 for key in gaps}
        found = {}
        for key in gaps:
            found[key] = {
                'rating': options['anchor_rating'] + SCALE * (mean[key] - mean[anchor]),
                'deviation': 0.0 if key == anchor else SCALE / math.sqrt(len(gaps[key]) + 1),
            }
        return found


def register(registry):
    registry.provide('rating_method/1', 'rating', GoalRating())
