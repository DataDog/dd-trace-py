To export an environment variable to your `.zshrc` file, you'll need to edit the file and add the export statement. Here are the steps to do that:

1. **Open Terminal**: First, open your terminal.

2. **Open .zshrc File**: Open the `.zshrc` file in a text editor. You can use `nano`, `vim`, or any other text editor you prefer. Here is how you can do it with `nano`:

    ```sh
    nano ~/.zshrc
    ```

    Or with `vim`:

    ```sh
    vim ~/.zshrc
    ```

3. **Add the Export Statement**: Add the environment variable export statement at the end of the file. For example, if you want to add an environment variable `MY_VARIABLE` with the value `my_value`, you would add:

    ```sh
    export MY_VARIABLE="my_value"
    ```

4. **Save and Close the File**: Save the file and close the text editor. In `nano`, you can do this by pressing `CTRL + X`, then `Y` to confirm changes, and `Enter` to save. In `vim`, you can do this by pressing `ESC` to ensure you are in normal mode, then type `:wq` and press `Enter`.

5. **Reload .zshrc**: Finally, reload the `.zshrc` file to apply the changes. You can do this by running:

    ```sh
    source ~/.zshrc
    ```

**Example:**

Here's an example where we add two environment variables `MY_VARIABLE` and `ANOTHER_VARIABLE`:

1. Open `.zshrc`:

    ```sh
    nano ~/.zshrc
    ```

2. Add the following lines:

    ```sh
    export MY_VARIABLE="my_value"
    export ANOTHER_VARIABLE="another_value"
    ```

3. Save and close the file.

4. Reload the `.zshrc` file:

    ```sh
    source ~/.zshrc
    ```

Now, the environment variables `MY_VARIABLE` and `ANOTHER_VARIABLE` should be available in your terminal sessions. You can verify this by running:

```sh
echo $MY_VARIABLE
echo $ANOTHER_VARIABLE
```