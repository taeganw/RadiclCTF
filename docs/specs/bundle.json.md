A bundle is outlined by a json file, similar to a problem.json. The specification can be found below.

| Field | Datatype | Required | Description |
|-------|----------|----------|-------------|
| author | String | Yes | Author of the bundle. |
| name | String | Yes | The name of the bundle. |
| description | String Template | Yes | Description for the bundle. |
| dependencies | Weightmap (See Below) | No | A Weightmap specifying the problem unlocking dependencies |

As shown in the above specification, problem unlocking is specified by the bundle.
The `dependencies` field in the `bundle.json` is a weightmap specifying how problems
should be unlocked. It has the form:

```json
{
  "problem2": {
    "threshold": 1,
    "weightmap": {
      "problem1": 1
    }
  },
  "problem4": {
    "threshold" : 1,
    "weightmap": {
      "problem2": 1,
      "problem3": 1
    }
  }
}
```

The unlocking is computed as follows. Any problem specified in `dependencies` will be unlocked only after
its threshold value is reached by the sum of the weights specified in `weightmap`.

The above example specifies the following: `problem2` will be unlocked after `problem1` is solved.
`problem4` will be unlocked after either `problem2` or `problem3` is solved.

This system allows for arbitrary control of how problems unlock.